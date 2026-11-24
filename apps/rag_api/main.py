"""API 앱(검색 API 계획 3.1). 띄우기: `uvicorn rag_api.main:app` (apps/ 를 import 경로에).

시작할 때 한 번 준비해 `app.state` 에 둡니다: 설정, 규칙 문구(rules.yaml), DB 풀, 온톨로지 사전, 부서 표,
임베딩 클라이언트(한 번 미리 부름), KnowledgeEngine,
답변 LLM 클라이언트(LLM_BASE_URL 이 비면 None).
인증은 이 앱에 없습니다: 모든 요청이 비로그인이고, 인증 모듈이 `dependency_overrides` 로 요청자 정보를 바꿔 끼웁니다(auth/context.py).
백엔드가 내는 4xx·5xx 는 모두 `{"error": {"code", "message", "trace_id"}}` 입니다(약속서 00 4절).
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from rag_api.adapters.rest.routes import router
from rag_api.contracts.common import ApiError, trace_id
from rag_api.knowledge_engine.engine import KnowledgeEngine
from rag_api.rules import values as rules
from rag_api.settings import departments, ontology
from rag_api.shared.config import Config
from rag_api.shared.embedding import OpenAIEmbeddingClient
from rag_api.shared.llm import LLMClient

log = logging.getLogger(__name__)
# 본문 크기 상한: 받는 도중에 끊고 413(약속서 00 4.2). 라우트를 정의할 때 정해져 설정이 아닌 상수. 더 큰 상한이 필요한 라우트는 라우트에 따로
MAX_BODY_BYTES = 64 * 1024
# DB 에 연결하지 못함 → 503(약속서 00 4.3). SQL 오류 등은 500. 풀이 다 차면 시간 제한 없이 기다리고,
# TimeoutError 는 새 연결을 맺을 때(DB_CONNECT_TIMEOUT_S)만 나며 OSError 하위라 여기서 잡힘.
# 그래서 다른 시간 초과(LLM 등)를 그 자리에서 잡지 않고 올리면 503 이 됨 — 그런 곳은 직접 잡을 것
DB_UNAVAILABLE = (OSError, asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError)


def _warm_up(cfg: Config) -> None:
    """모델을 올리는 첫 호출(약 1초)이 질의 시간 제한(EMBED_TIMEOUT_S)에 걸리지 않게 미리 부름. 실패해도 시작."""
    try:
        OpenAIEmbeddingClient(cfg.embed_base_url, cfg.embed_model, cfg.embed_warmup_timeout_s,
                              cfg.embed_api_key).embed_query("준비")
    except Exception as e:
        log.warning("임베딩 미리 부르기 실패, 그대로 시작: %s: %s", type(e).__name__, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = Config.from_env()
    pack = departments.active_pack(cfg.active_packs)
    app.state.rules = rules.load(cfg.rules_path)  # 빠진 키면 DB 에 붙기 전에 멈춤
    # DB 의 시간대 없는 TIMESTAMP 는 한국 시각(경계 계약 v0.28 1.1). 설치의 DB 기본 시간대 설정이 빠져도 같게 연결에도 정함
    pool = await asyncpg.create_pool(cfg.database_url, min_size=cfg.db_pool_size, max_size=cfg.db_pool_size,
                                     timeout=cfg.db_connect_timeout_s, server_settings={"timezone": "Asia/Seoul"})
    llm = None
    try:
        async with pool.acquire() as conn:
            # ponytail: 사전은 시작 때 한 번. 주기 다시 읽기(settings.ontology)는 사전 관리 기능을 만들 때
            await ontology.reload_if_changed(conn)
            dept_names, _ = await departments.load(conn, pack)
        if cfg.vector_channel_enabled:
            await asyncio.to_thread(_warm_up, cfg)
        embedder = OpenAIEmbeddingClient(cfg.embed_base_url, cfg.embed_model, cfg.embed_timeout_s, cfg.embed_api_key)
        app.state.cfg, app.state.pool, app.state.active_pack, app.state.departments = cfg, pool, pack, dept_names
        app.state.engine = KnowledgeEngine(pool, embedder, cfg)
        # ponytail: 첫 호출의 모델 올리기(개발 약 6초)를 미리 하지 않음. 첫 답변이 시간 초과로 나오면 임베딩처럼 미리 부름
        app.state.llm = llm = LLMClient(cfg) if cfg.llm_base_url else None
        yield
    finally:
        if llm:
            await llm.close()
        await pool.close()


def _error(request: Request, http_status: int, code: str, message: str, headers=None) -> JSONResponse:
    headers = dict(headers or {})
    if http_status == 401:  # 모든 401 에(RFC 9110 15.5.2, 약속서 00 4.2). 본문은 그대로
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse({"error": {"code": code, "message": message, "trace_id": trace_id(request)}},
                        status_code=http_status, headers=headers)


def create_app() -> FastAPI:
    app = FastAPI(title="rag_api", lifespan=lifespan, max_body_size=MAX_BODY_BYTES)
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _error(request, exc.http_status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: RequestValidationError):
        # 위치와 이유만. 입력값은 넣지 않음
        message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _error(request, 422, "VALIDATION_FAILED", message)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):  # 없는 주소(404)·맞지 않는 메서드(405) 등
        code = "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_FAILED" if exc.status_code < 500 else "INTERNAL_ERROR"
        return _error(request, exc.status_code, code, str(exc.detail), exc.headers)  # 405 의 Allow 등

    async def db_unavailable(request: Request, exc: Exception):
        log.error("DB 에 연결하지 못함 trace_id=%s", trace_id(request), exc_info=exc)
        return _error(request, 503, "SERVICE_UNAVAILABLE", "잠시 뒤 다시 시도해 주세요.")

    for exc_type in DB_UNAVAILABLE:
        app.add_exception_handler(exc_type, db_unavailable)

    @app.exception_handler(asyncpg.PostgresError)
    async def db_error(request: Request, exc: asyncpg.PostgresError):
        # 클래스·sqlstate·제약 이름만. asyncpg 오류 글의 DETAIL 에는 실패한 행 전체(이메일·해시 등)가 들어 있음.
        # 연결 장애(DB_UNAVAILABLE)는 더 구체적인 위 처리기가 먼저 받음
        log.error("처리하지 못한 DB 오류 trace_id=%s %s sqlstate=%s constraint=%s", trace_id(request), type(exc).__name__,
                  getattr(exc, "sqlstate", None), getattr(exc, "constraint_name", None))
        return _error(request, 500, "INTERNAL_ERROR", "서버 오류입니다. trace_id 와 함께 문의해 주세요.")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.error("처리하지 못한 오류 trace_id=%s", trace_id(request), exc_info=exc)
        return _error(request, 500, "INTERNAL_ERROR", "서버 오류입니다. trace_id 와 함께 문의해 주세요.")

    return app


app = create_app()
