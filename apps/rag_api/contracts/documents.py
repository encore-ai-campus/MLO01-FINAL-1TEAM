"""원문 보기 출력(원문보기 구현계획 1.2). 점수·검색 추적은 담지 않습니다."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from rag_api.contracts.qa import ConflictClause


class VersionItem(BaseModel):
    """버전 선택기 항목. 요청자가 열 수 있는 판만."""
    version_id: UUID
    version_tag: str
    effective_from: date
    effective_to: date | None
    is_current: bool


class Location(BaseModel):
    """`chunk_id` 로 연 조항 위치. 청크가 정리됐으면 found=false 이고 나머지는 null."""
    chunk_id: str
    found: bool
    section_path: str | None
    text: str | None  # body 안에서 그대로 찾히는 글(합친 조각은 body 에서 잘라 낸 것). 못 정하면 조각 본문. 화면이 찾아 형광펜


class WorkflowStep(BaseModel):
    """협업 흐름도 단계(약속서 회신 §3 ①: ontology_graph_meta 와 같은 이름). source_doc_id 는 엔진이 더함."""
    step: int
    doc_id: str
    title: str
    dept_code: str
    is_active: bool
    source_doc_id: str
    required_form_id: str | None = None  # 서식이 볼 수 있는 현행 문서일 때만 있음


class Workflow(BaseModel):
    workflow_name: str | None = None
    current_step: int | None = None
    prev_steps: list[WorkflowStep] = []
    next_steps: list[WorkflowStep] = []
    conflicts: list[ConflictClause] = []  # 03B 의 충돌 카드와 같은 모양


class DocumentResponse(BaseModel):
    doc_id: str
    title: str
    doc_type: str
    source_tier: int
    dept_owner: str
    dept_name: str
    version_id: UUID
    version_tag: str
    effective_from: date
    effective_to: date | None
    is_current: bool
    version_badge: Literal["scheduled", "previous", "unverified"] | None
    body: str
    location: Location | None
    versions: list[VersionItem]
    workflow: Workflow | None
    trace_id: str
