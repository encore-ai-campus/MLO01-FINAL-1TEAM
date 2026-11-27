"""문서·용어 바로 찾기 입출력(약속서 03A 2·3·4절). 점수 원값·검색 추적은 담지 않습니다."""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from rag_api.contracts.common import DiscoverableNotice, PgText, Status

DocType = Literal["official_policy", "standard_template", "ojt_guide", "benchmark_reference"]  # 약속서 00 5절


class SearchRequest(BaseModel):
    query: Annotated[PgText, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]  # 앞뒤 공백은 지우고 셈
    dept_filter: list[Annotated[PgText, Field(max_length=16)]] = Field(default=[], max_length=10)  # 입력 한도(adapters.md §1)
    doc_types: list[DocType] = Field(default=[], max_length=4)
    limit: int = Field(default=10, ge=1, le=30)
    include_benchmark: bool = False
    cursor: PgText | None = Field(default=None, max_length=64)


class Snippet(BaseModel):
    chunk_id: str
    section_path: str
    text: str


class DocumentHit(BaseModel):
    doc_id: str
    title: str
    dept_owner: str
    dept_name: str
    doc_type: str
    source_tier: int
    version_id: UUID
    version_tag: str
    effective_from: date
    version_badge: Literal["scheduled", "unverified"] | None
    snippets: list[Snippet]


class TermCard(BaseModel):
    concept_id: str
    canonical_term: str
    definition: str
    matched_text: str
    dept_owner: str | None


class ConceptChip(BaseModel):
    concept_id: str
    canonical_term: str


class SearchResponse(BaseModel):
    total: int
    active_pack: str
    documents: list[DocumentHit]
    reference_templates: list[DocumentHit]  # 공공 표준 양식(standard_template)
    reference_cases: list[DocumentHit]      # 외부 참고 사례(benchmark_reference). include_benchmark 일 때만 채워짐
    term_cards: list[TermCard]
    concepts: list[ConceptChip]
    notices: list[DiscoverableNotice]
    next_cursor: str | None
    status: Status
    trace_id: str
