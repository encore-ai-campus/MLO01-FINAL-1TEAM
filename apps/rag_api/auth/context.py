"""요청자 판정 결과와 의존성. 검색·QA·관리 기능은 이것만 봅니다(auth.md §1, 인증 경계 1~2절)."""
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel

from rag_api.contracts.common import ApiError


class AuthContext(BaseModel):
    principal_type: Literal["user", "anonymous", "system"]
    user_id: UUID | None = None          # users.user_id (user 일 때)
    status: Literal["PENDING", "REVISION_REQ", "REJECTED", "ACTIVE"] | None = None  # INACTIVE 는 관문에서 막힘
    dept_code: str | None = None         # users.dept_code
    extra_depts: list[str] = []          # users.extra_depts (겸직)
    role: Literal["ADMIN", "DEPT_MANAGER", "EMPLOYEE", "NEW_HIRE"] | None = None
    active_pack: str                     # pack_config.json 에서 읽음 (2차 D3)
    snapshot_at: datetime                # 판정에 쓴 스냅샷 시각 (감사용)


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "로그인이 필요하거나 토큰이 만료·위조됐습니다.")


async def get_auth_context(request: Request) -> AuthContext:
    """FastAPI 의존성. 요청한 사람의 정보. 기본은 늘 비로그인(공개 문서만 보임). 정보가 없을 때 더 많이 보이는 일은 없음.
    ponytail: 인증 모듈이 dependency_overrides 로 바꿔 끼움(인증 경계 2절)."""
    return AuthContext(principal_type="anonymous", active_pack=request.app.state.active_pack, snapshot_at=datetime.now(UTC))


async def require_active(ctx: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
    """FastAPI 의존성. 승인된 계정(ACTIVE)만 쓰는 기능(AI 답변 등, 약속서 00 2절). 의존성이라 본문 검증(422)보다 먼저
    401·403 을 냄. 막힌 계정은 인증 모듈이 이미 403."""
    if ctx.principal_type != "user":
        raise unauthenticated()
    if ctx.status in ("PENDING", "REVISION_REQ"):
        raise ApiError(403, "ACCOUNT_PENDING", "승인 전 계정입니다.")
    if ctx.status == "REJECTED":
        raise ApiError(403, "ACCOUNT_REJECTED", "가입이 거절된 계정입니다.")
    return ctx
