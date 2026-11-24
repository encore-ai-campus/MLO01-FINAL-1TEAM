"""REST 라우트(약속서 03A·03B·03D): 문서 검색·AI 답변·원문 보기. 판정·데이터 접근은 기능 모듈이 함(adapters.md).
요청자 정보는 `context.get_auth_context`(기본 비로그인)로 받고, 인증 모듈이 그 함수를 바꿔 끼웁니다(인증 경계 2절)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from rag_api.auth import context
from rag_api.auth.context import AuthContext
from rag_api.contracts.common import PgText, trace_id
from rag_api.contracts.documents import DocumentResponse
from rag_api.contracts.qa import QARequest, QAResponse
from rag_api.contracts.search import SearchRequest, SearchResponse
from rag_api.documents import service as documents
from rag_api.qa import service as qa
from rag_api.search import service as search

router = APIRouter(prefix="/api/v1")
Ctx = Annotated[AuthContext, Depends(context.get_auth_context)]
Active = Annotated[AuthContext, Depends(context.require_active)]  # 승인된 계정만. 본문 검증보다 먼저 401·403


def client_ip(request: Request) -> str | None:
    """감사 로그의 접속 IP. 지금은 직접 붙은 주소(프록시 뒤의 실제 IP 는 인프라 확인 뒤)."""
    return request.client.host if request.client else None


@router.post("/search/documents")
async def search_documents(body: SearchRequest, request: Request, ctx: Ctx) -> SearchResponse:
    """비로그인도 됨(PUBLIC 만, 약속서 00 2절)."""
    s = request.app.state
    return await search.search_documents(s.engine, s.departments, s.rules, body, ctx, trace_id(request))


@router.post("/qa/answers")
async def qa_answers(body: QARequest, request: Request, ctx: Active) -> QAResponse:
    """승인된 계정(ACTIVE)만(약속서 03B 1절)."""
    s = request.app.state
    return await qa.answer(s.engine, s.llm, s.rules, s.departments, s.qa_departments, body, ctx, trace_id(request),
                           pool=s.pool, ip=client_ip(request))


@router.get("/documents/{doc_id}")
async def get_document(request: Request, response: Response, ctx: Ctx, doc_id: Annotated[PgText, Path(min_length=1, max_length=64)],
                       version_tag: Annotated[PgText, Query(min_length=1, max_length=32)] | None = None,
                       chunk_id: Annotated[PgText, Query(min_length=1, max_length=128)] | None = None) -> DocumentResponse:
    """원문 보기(documents.md §6). 비로그인도 됨(볼 수 있는 문서만). 요청자마다 범위가 달라 캐시에 남기지 않음."""
    s = request.app.state
    response.headers["Cache-Control"] = "private, no-store"
    return await documents.view(s.engine, s.pool, s.departments, s.rules, doc_id, version_tag, chunk_id, ctx, trace_id(request),
                                ip=client_ip(request))
