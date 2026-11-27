"""공통 응답·오류 모양(약속서 00 3·4절)과 검색·답변이 같이 쓰는 카드·배지."""
import calendar
import uuid
from datetime import date
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
    """기능이 내는 4xx. main 의 처리기가 `{"error": {...}}` 본문으로 바꿈. `extra` 는 `error` 에 더할 필드(원문 403 의 소관 부서 안내, 약속서 00 4.2)."""

    def __init__(self, http_status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(code)
        self.http_status, self.code, self.message, self.extra = http_status, code, message, extra or {}


_PREFIXES = (("/api/v1/auth", "auth"), ("/api/v1/departments", "auth"), ("/admin/v1", "adm"), ("/api/v1/search", "srch"), ("/api/v1/qa", "qa"),
             ("/api/v1/documents", "doc"))


def trace_id(request: Request) -> str:
    """요청마다 하나. 접두사(auth-·srch-·qa-·doc-·adm-, 부서 목록도 auth-) + 무작위 값."""
    if not hasattr(request.state, "trace_id"):
        prefix = next((p for path, p in _PREFIXES if request.url.path.startswith(path)), "req")
        request.state.trace_id = f"{prefix}-{uuid.uuid4().hex}"
    return request.state.trace_id


def add_months(d: date, months: int) -> date:
    """달력 개월 더하기. 달 끝은 그달 마지막 날로 맞춤(2026-08-31 + 6 = 2027-02-28)."""
    year, month = divmod(d.month - 1 + months, 12)
    year, month = d.year + year, month + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def is_unverified(basis: date, long_interval: bool, today: date, cfg) -> bool:
    """기준 날짜 + 점검 주기(긴 주기 12개월, 아니면 6개월. 경계 계약 4.11)가 오늘보다 앞이면 낡음. 같은 날까지는 아님."""
    return add_months(basis, cfg.verify_long_interval_months if long_interval else cfg.verify_interval_months) < today


def version_badge(effective_from: date, today: date, *, stale: bool = False) -> Literal["scheduled", "unverified"] | None:
    """시행일이 오늘(한국 날짜)보다 뒤면 시행 예정(약속서 03A·03B 5-08). 아니면 낡은 문서(`stale`)면 unverified.
    값은 하나만 나가므로 scheduled 가 앞섬(경계 계약 4.11)."""
    if effective_from > today:
        return "scheduled"
    return "unverified" if stale else None


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
