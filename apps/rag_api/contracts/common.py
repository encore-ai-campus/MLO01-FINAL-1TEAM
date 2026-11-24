"""공통 응답·오류 모양(약속서 00 3·4절)과 검색·답변이 같이 쓰는 카드·배지."""
import uuid
from datetime import date, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Request
from pydantic import BaseModel, Field

from rag_api.knowledge_engine.types import DiscoverHint

# 요청의 문자열 입력. PostgreSQL text 는 NUL 을 받지 않아(그대로 넘기면 500) 계약에서 422 로 막음
PgText = Annotated[str, Field(pattern=r"^[^\x00]*$")]


class Status(BaseModel):
    complete: bool = True
    degraded: bool = False
    reason_codes: list[str] = []


class ApiError(Exception):
    """기능이 내는 4xx. main 의 처리기가 `{"error": {...}}` 본문으로 바꿈."""

    def __init__(self, http_status: int, code: str, message: str):
        super().__init__(code)
        self.http_status, self.code, self.message = http_status, code, message


_PREFIXES = (("/api/v1/auth", "auth"), ("/api/v1/departments", "auth"), ("/admin/v1", "adm"), ("/api/v1/search", "srch"), ("/api/v1/qa", "qa"),
             ("/api/v1/documents", "doc"))


def trace_id(request: Request) -> str:
    """요청마다 하나. 접두사(auth-·srch-·qa-·doc-·adm-, 부서 목록도 auth-) + 무작위 값."""
    if not hasattr(request.state, "trace_id"):
        prefix = next((p for path, p in _PREFIXES if request.url.path.startswith(path)), "req")
        request.state.trace_id = f"{prefix}-{uuid.uuid4().hex}"
    return request.state.trace_id


# 한국은 1988년 이후 서머타임 없음. 배지의 "오늘"은 서버 시간대와 관계없이 한국 날짜(검색 API 계획 6절)
KST = timezone(timedelta(hours=9))


def version_badge(effective_from: date, today: date) -> Literal["scheduled"] | None:
    """시행일이 오늘(한국 날짜)보다 뒤면 시행 예정(약속서 03A·03B 5-08)."""
    return "scheduled" if effective_from > today else None


class DiscoverableNotice(BaseModel):
    """존재 공개 안내 카드(약속서 03A 4절, 03B 3.3 은 같은 카드)."""
    kind: Literal["discoverable"]
    title: str
    dept_owner: str
    dept_name: str
    text: str


def discoverable_notices(hints: list[DiscoverHint], departments: dict[str, str], template: str) -> list[DiscoverableNotice]:
    """엔진의 DiscoverHint 를 카드로. 부서 이름은 활성 팩 표시 이름(약속서 00 5절), 문구는 rules.yaml 협조전 안내."""
    cards = []
    for h in hints:
        name = departments.get(h.dept_owner, h.dept_name)
        cards.append(DiscoverableNotice(kind="discoverable", title=h.title, dept_owner=h.dept_owner, dept_name=name,
                                        text=template.format(dept_name=name)))
    return cards
