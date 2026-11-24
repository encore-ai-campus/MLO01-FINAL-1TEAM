"""앱 시작·오류 처리·기본 요청자(검색 API 계획 4.1, 인증분리 계획 1.3). DB 없이 가짜 풀로 HTTP 까지.
인증은 이 앱에 없다: 요청자 정보의 기본은 비로그인이고 인증 모듈이 바꿔 끼웁니다(auth/context.py)."""
import asyncio
import dataclasses
import logging
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest
from asgi_client import request
from fastapi import Request
from test_rules import rules_file

from rag_api import main
from rag_api.auth import context
from rag_api.auth.context import AuthContext
from rag_api.main import create_app
from rag_api.settings import departments
from rag_api.shared.config import Config

@pytest.fixture
def app():
    app = create_app()  # lifespan 대신 상태를 직접 둠(ASGITransport 는 lifespan 을 돌리지 않음)
    app.state.cfg = Config(database_url="x")
    app.state.pool, app.state.active_pack, app.state.departments = None, "it_service", {}
    return app


def get_context(app, headers: dict) -> AuthContext:
    scope = {"type": "http", "app": app, "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    return asyncio.run(context.get_auth_context(Request(scope)))


# --- 기본 요청자(인증 경계 2절) ------------------------------------------------------------------

def test_default_requester_is_always_anonymous(app):
    """인증 모듈이 없으면 어떤 헤더가 와도 비로그인: 정보가 없을 때 더 많이 보이는 일은 없음(인증 경계 1절 조건 2). 요청자 정보는
    클라이언트가 보낸 값으로 채우지 않음."""
    for headers in ({}, {"Authorization": "Bearer abc.def.ghi"}, {"Authorization": "Basic Zm9vOmJhcg=="},
                    {"X-Dept-Code": "HR", "X-Role": "ADMIN", "X-User-Id": str(uuid.UUID(int=2)), "Cookie": "token=x",
                     "X-Test-User": "admin@company.com"}):  # 마지막은 본체 DB 시험이 쓰는 헤더: 앱 코드가 읽으면 안 됨
        ctx = get_context(app, headers)
        assert (ctx.principal_type, ctx.user_id, ctx.status, ctx.dept_code, ctx.extra_depts, ctx.role, ctx.active_pack) == (
            "anonymous", None, None, None, [], None, "it_service"), headers


def test_body_app_has_no_auth_routes_or_overrides(app):
    """본체 앱에는 인증·관리·부서 목록 라우트가 없고 바꿔 끼운 것도 없음(그것은 인증 모듈의 일)."""
    assert app.dependency_overrides == {}
    for method, path in (("POST", "/api/v1/auth/login"), ("GET", "/api/v1/auth/me"), ("POST", "/api/v1/auth/signup"),
                         ("GET", "/api/v1/departments"), ("GET", "/admin/v1/users")):
        assert request(app, method, path).status_code in (404, 405), path


# --- 오류 모양 ----------------------------------------------------------------------------------------------------

def test_error_bodies(app, caplog):
    r = request(app, "GET", "/api/v1/nowhere")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")

    @app.get("/api/v1/search/boom")
    async def boom():
        raise RuntimeError("민감한 내부 내용")
    with caplog.at_level(logging.ERROR, logger="rag_api.main"):
        r = request(app, "GET", "/api/v1/search/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR" and "민감한" not in r.text
    assert any(r.json()["error"]["trace_id"] in rec.getMessage() and rec.exc_info for rec in caplog.records)


@pytest.mark.parametrize("exc, status", [
    (ConnectionRefusedError("연결 거부 내부 내용"), 503), (TimeoutError(), 503), (asyncpg.CannotConnectNowError("x"), 503),
    (asyncpg.TooManyConnectionsError("x"), 503), (asyncpg.PostgresConnectionError("x"), 503),
    (asyncpg.UndefinedTableError("x"), 500)])  # SQL 오류는 연결 장애가 아니라 500
def test_db_unavailable_is_503(app, caplog, exc, status):
    class DownPool:
        async def fetchrow(self, sql, *args):
            raise exc
    app.state.pool = DownPool()

    @app.get("/api/v1/search/probe")
    async def probe(request: Request):
        await request.app.state.pool.fetchrow("SELECT 1")
    with caplog.at_level(logging.ERROR, logger="rag_api.main"):
        r = request(app, "GET", "/api/v1/search/probe")
    code = {503: "SERVICE_UNAVAILABLE", 500: "INTERNAL_ERROR"}[status]
    assert (r.status_code, r.json()["error"]["code"]) == (status, code) and "내부 내용" not in r.text
    assert any(r.json()["error"]["trace_id"] in rec.getMessage() for rec in caplog.records)


def test_unhandled_db_error_logs_only_class_sqlstate_constraint(app, caplog):
    secret = "Key (email)=(secret@example.test, $2b$10$abcdefghij) already exists."

    @app.get("/api/v1/search/boom-db")
    async def boom():
        e = asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
        e.detail, e.constraint_name = secret, "users_email_key"
        raise e
    with caplog.at_level(logging.DEBUG):
        r = request(app, "GET", "/api/v1/search/boom-db")
    assert (r.status_code, r.json()["error"]["code"]) == (500, "INTERNAL_ERROR") and "secret" not in r.text
    assert [x.getMessage() for x in caplog.records if x.name == "rag_api.main"] == [
        f"처리하지 못한 DB 오류 trace_id={r.json()['error']['trace_id']} UniqueViolationError sqlstate=23505 constraint=users_email_key"]
    assert "secret" not in caplog.text and "$2b$" not in caplog.text and all(x.exc_info is None for x in caplog.records)


# --- 앱 시작 ------------------------------------------------------------------------------------------------------

def startup_runner(monkeypatch, create_app):
    """lifespan 을 DB·임베딩 서버 없이 돌리는 함수를 만듦. 앱 팩토리(`create_app`)를 받습니다. 설정 환경 변수는 모두 지운 뒤 여기서
    정함(셸 값에 흔들리지 않게).
    `run(**환경 변수)` → 시작한 앱(값이 None 이면 그 변수를 지움). 풀 만들기 인자는 app.state.pool.kwargs, 가짜 풀을 만든 횟수는
    `run.pool_calls`(시작이 DB 에 붙기 전에 멈췄는지 봄)."""
    class Pool:
        def __init__(self, kwargs):
            self.kwargs = kwargs

        @asynccontextmanager
        async def acquire(self):
            yield None

        async def close(self):
            pass

    pool_calls = []

    async def create_pool(dsn, **kw):
        pool_calls.append(dsn)
        return Pool(kw)

    async def no_op(*args):
        return {}

    async def no_depts(conn, pack):
        return {}, departments.QADepartments({}, {})

    class FakeLLM:  # 실제 LLM 서버에 닿지 않게(qa 계획 4.2)
        def __init__(self, cfg):
            self.cfg, self.closed = cfg, False

        async def close(self):
            self.closed = True

    monkeypatch.setattr(main.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(main.ontology, "reload_if_changed", no_op)
    monkeypatch.setattr(main.departments, "load", no_depts)
    monkeypatch.setattr(main, "LLMClient", FakeLLM)
    for f in dataclasses.fields(Config):
        monkeypatch.delenv(f.name.upper(), raising=False)

    def run(**env):
        base = {"DATABASE_URL": "postgresql://unused/x", "ACTIVE_PACKS": "base,it_service",
                "VECTOR_CHANNEL_ENABLED": "false", "EMBED_BASE_URL": "http://unused.invalid/v1", "EMBED_MODEL": "unused",
                "EMBED_TIMEOUT_S": "0.5", "LLM_BASE_URL": "http://unused.invalid/v1", "LLM_MODEL": "unused"}
        for k, v in {**base, **env}.items():
            monkeypatch.delenv(k, raising=False) if v is None else monkeypatch.setenv(k, v)

        async def go():
            app = create_app()
            async with app.router.lifespan_context(app):
                app.state.running_tasks = [t.get_coro().__name__ for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            app.state.leftover_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return app
        return asyncio.run(go())
    run.pool_calls = pool_calls
    return run


@pytest.fixture
def startup(monkeypatch):
    return startup_runner(monkeypatch, create_app)


@pytest.mark.parametrize("env, error", [
    ({"ACTIVE_PACKS": "it_service"}, ValueError), ({"ACTIVE_PACKS": "base,benchmark,it_service"}, ValueError),
    ({"LLM_MODEL": None}, ValueError)])  # LLM 주소가 있는데 모델이 없음
def test_app_does_not_start_with_invalid_packs_or_llm_settings(startup, env, error):
    with pytest.raises(error):
        startup(**env)
    assert startup.pool_calls == []  # DB 에 붙기 전에 멈춤: 가짜 풀을 한 번도 만들지 않음


def test_app_starts_with_defaults(startup):
    app = startup()
    assert app.state.engine is not None and app.state.active_pack == "it_service"
    # 기본값 = asyncpg 기본 풀·연결 제한 5초. DB 시간대 설정과 무관하게 연결은 한국 시각(auth 계획 3.1)
    assert app.state.pool.kwargs == {"min_size": 10, "max_size": 10, "timeout": 5.0, "server_settings": {"timezone": "Asia/Seoul"}}
    assert app.state.rules.cooperation_request.startswith("본 문서는 {dept_name}")  # 패키지 안 rules.yaml
    assert not hasattr(app.state, "hasher")  # 인증용 작업자는 본체가 만들지 않음


def test_startup_reads_pool_and_rules_settings(startup, tmp_path):
    app = startup(DB_POOL_SIZE="3", DB_CONNECT_TIMEOUT_S="2.5")
    assert app.state.pool.kwargs == {"min_size": 3, "max_size": 3, "timeout": 2.5,
                                     "server_settings": {"timezone": "Asia/Seoul"}}
    ok = rules_file(tmp_path, cooperation_request="{dept_name} 문의")
    assert startup(RULES_PATH=str(ok)).state.rules.cooperation_request == "{dept_name} 문의"
    (tmp_path / "bad.yaml").write_text("other: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cooperation_request"):  # 빠진 키면 시작하지 않음
        startup(RULES_PATH=str(tmp_path / "bad.yaml"))


def test_llm_client_from_settings_closed_at_end(startup):
    app = startup()
    assert app.state.llm.cfg.llm_base_url == "http://unused.invalid/v1" and app.state.llm.closed  # 앱이 끝날 때 닫음
    assert startup(LLM_BASE_URL=None, LLM_MODEL=None).state.llm is None  # 주소가 비면 LLM 없이(qa 가 대체 응답)


def test_active_pack():
    assert departments.active_pack("base,it_service") == departments.active_pack(" it_service , base ") == "it_service"
    for bad in ("", "base", "it_service", "base,it_service,ecommerce", "base,benchmark", "base,it_service,benchmark"):
        with pytest.raises(ValueError):
            departments.active_pack(bad)


def test_departments_load_names_parts_and_ojt():
    class Conn:
        async def fetch(self, sql):
            return [{"dept_code": "SLS", "default_name": "영업", "labels": '{"it_service": "B2B영업·마케팅"}', "ojt": '["할인율?"]'},
                    {"dept_code": "OPS", "default_name": "경영운영·총무", "labels": "{}", "ojt": "[]"}]
    names, qa = asyncio.run(departments.load(Conn(), "it_service"))
    assert names == {"SLS": "B2B영업·마케팅", "OPS": "경영운영·총무"}  # 활성 팩 라벨, 없으면 default_name
    assert qa.name_parts == {"SLS": ("B2B영업", "마케팅", "영업"), "OPS": ("경영운영", "총무")}  # 라벨·default_name 조각
    assert qa.ojt_questions == {"SLS": ["할인율?"], "OPS": []}
    assert asyncio.run(departments.load(Conn(), "ecommerce"))[0] == {"SLS": "영업", "OPS": "경영운영·총무"}


def test_app_starts_when_embedding_warm_up_fails(startup, monkeypatch, caplog):
    timeouts = []

    class DownEmbedder:
        def __init__(self, base_url, model, timeout_s, api_key):
            timeouts.append(timeout_s)

        def embed_query(self, text):
            raise ConnectionError("임베딩 서버 없음")

    monkeypatch.setattr(main, "OpenAIEmbeddingClient", DownEmbedder)
    with caplog.at_level(logging.WARNING, logger="rag_api.main"):
        app = startup(VECTOR_CHANNEL_ENABLED="true")  # 미리 부르기가 실패해도 시작함
    assert app.state.engine is not None
    assert timeouts == [app.state.cfg.embed_warmup_timeout_s, 0.5] == [3.0, 0.5]  # 미리 부르기는 별도 상한, 질의용은 EMBED_TIMEOUT_S
    assert [r.getMessage() for r in caplog.records] == ["임베딩 미리 부르기 실패, 그대로 시작: ConnectionError: 임베딩 서버 없음"]
    timeouts.clear()
    startup(VECTOR_CHANNEL_ENABLED="true", EMBED_WARMUP_TIMEOUT_S="1.5")
    assert timeouts == [1.5, 0.5]
