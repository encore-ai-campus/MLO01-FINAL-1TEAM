"""AI 답변 입출력(약속서 03B 2·3절). `result` 마다 다른 모델이고 03B 예시에 보이는 키만 냅니다."""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from rag_api.contracts.common import DiscoverableNotice, PgText, Status


class QARequest(BaseModel):
    question: Annotated[PgText, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]  # 앞뒤 공백은 지우고 셈
    doc_ids: list[Annotated[PgText, Field(max_length=64)]] = Field(default=[], max_length=10)  # documents.doc_id VARCHAR(64)
    previous_query: Annotated[PgText, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class GuideNotice(BaseModel):
    """글만 있는 안내(03B 3.3 personal_data_guide·legal_disclaimer)."""
    kind: Literal["personal_data_guide", "legal_disclaimer"]
    text: str


class ScheduledNotice(BaseModel):
    """시행 예정 개정판 안내(03B 3.3, 5-08). 인용 문서마다."""
    kind: Literal["scheduled_revision"]
    doc_id: str
    effective_from: date
    text: str


class Sentence(BaseModel):
    text: str
    citations: list[str]  # 근거 chunk_id. 모두 evidence 안


class Evidence(BaseModel):
    """답변에 쓴 근거 조항(03B 3.1). 그 밖의 필드는 03A documents 와 같은 뜻."""
    chunk_id: str
    doc_id: str
    title: str
    dept_owner: str
    dept_name: str
    section_path: str
    clause_label: str  # 화면 표시용 [문서명 제N조]
    text: str          # 조항 전문(chunks.content)
    doc_type: str      # 사규·표준 양식·외부 참고 사례를 가르는 기준(source_tier 로는 표준 양식과 외부 사례를 못 가름)
    source_tier: int
    source_badge: Literal["official"] | None
    version_id: UUID
    version_tag: str
    effective_from: date
    version_badge: Literal["scheduled"] | None


class ConflictClause(BaseModel):
    """전사·부서 충돌 병기 카드(03B 3.2). 그래프 메타 conflicts 항목 그대로 + 이 충돌이 걸린 근거 문서."""
    source_doc_id: str
    target_doc_id: str
    target_title: str
    clause_pair: str
    priority_rule: str


class Excerpt(BaseModel):
    """대체 응답의 원문 발췌(03B 3.5)."""
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    section_path: str
    text: str
    effective_from: date
    version_badge: Literal["scheduled"] | None


class Answered(BaseModel):
    result: Literal["answered"] = "answered"
    header: str | None  # 인용에 1순위가 없고 2순위만일 때의 머리말(R1)
    answer: list[Sentence]
    evidence: list[Evidence]
    conflict_clauses: list[ConflictClause]
    notices: list[DiscoverableNotice | ScheduledNotice | GuideNotice]
    status: Status
    trace_id: str


class InsufficientEvidence(BaseModel):
    result: Literal["insufficient_evidence"] = "insufficient_evidence"
    message: str
    notices: list[DiscoverableNotice | GuideNotice]
    suggested_questions: list[str]
    status: Status
    trace_id: str


class FallbackExcerpts(BaseModel):
    result: Literal["fallback_excerpts"] = "fallback_excerpts"
    excerpts: list[Excerpt]
    notices: list[DiscoverableNotice | ScheduledNotice | GuideNotice]  # 답변과 같은 모양. 지금은 존재 공개 안내만
    status: Status
    trace_id: str


QAResponse = Annotated[Answered | InsufficientEvidence | FallbackExcerpts, Field(discriminator="result")]
