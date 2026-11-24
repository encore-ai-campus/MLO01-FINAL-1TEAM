"""REST 라우트(약속서 03A·03B): 문서 검색·AI 답변. 판정·데이터 접근은 기능 모듈이 함(adapters.md).
요청자 정보는 `context.get_auth_context`(기본 비로그인)로 받고, 인증 모듈이 그 함수를 바꿔 끼웁니다(인증 경계 2절)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from rag_api.auth import context
from rag_api.auth.context import AuthContext
from rag_api.contracts.common import trace_id
from rag_api.contracts.qa import QARequest, QAResponse
from rag_api.contracts.search import SearchRequest, SearchResponse
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
