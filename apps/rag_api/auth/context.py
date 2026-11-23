"""요청자 판정 결과. 검색·QA·관리 기능은 이것만 봅니다(auth.md §1)."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class AuthContext(BaseModel):
    principal_type: Literal["user", "anonymous", "system"]
    user_id: UUID | None = None          # users.user_id (user 일 때)
    status: Literal["PENDING", "REVISION_REQ", "REJECTED", "ACTIVE"] | None = None  # INACTIVE 는 관문에서 막힘
    dept_code: str | None = None         # users.dept_code
    extra_depts: list[str] = []          # users.extra_depts (겸직)
    role: Literal["ADMIN", "DEPT_MANAGER", "EMPLOYEE", "NEW_HIRE"] | None = None
    active_pack: str                     # pack_config.json 에서 읽음 (2차 D3)
    snapshot_at: datetime                # 판정에 쓴 스냅샷 시각 (감사용)
