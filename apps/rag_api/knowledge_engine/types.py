"""knowledge_engine 안쪽 형. 밖에 내보내는 계약(Pydantic)은 `contracts/` 가 따로 둡니다."""
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    strategy: Literal["hybrid", "graph_hybrid"] = "hybrid"  # 요청이 아니라 부르는 쪽(서버 코드)이 정함
    dept_filter: tuple[str, ...] = ()  # 소관 부서(dept_owner). 열람 조건 위에 더함
    doc_types: tuple[str, ...] = ()
    include_benchmark: bool = False
    doc_ids: tuple[str, ...] = ()  # 사용자가 고른 문서 안에서만(qa 5-13). 열람 조건 위에 더함


@dataclass(frozen=True)
class DocMeta:
    doc_id: str
    title: str
    version_tag: str
    doc_type: str
    source_tier: int
    dept_owner: str
    effective_from: date
    version_id: UUID  # 현행 버전(약속서 03A version_id)


@dataclass
class RetrievedChunk:
    chunk_id: str
    section_path: str
    content: str
    lexemes: list[str]  # tsvector_to_array(tsv)
    doc: DocMeta
    # 점수 원값은 search·qa 안에서만 씁니다. 응답에 내지 않습니다(설계 §2.2: 권한 밖 단어 빈도를 짐작할 수 있음)
    bm25: float | None = None        # BM25 점수(클수록 잘 맞음). 키워드 채널에 없거나 토큰이 하나도 안 맞으면 None
    keyword_rank: int | None = None  # 키워드 목록 순위(1부터)
    matched_tokens: int = 0          # 키워드 질의 토큰 중 lexemes 에 있는 수
    cosine: float | None = None      # 벡터 채널에 없으면 None
    graph_rank: int | None = None    # 그래프 채널(graph_hybrid) 목록 순위
    rrf: float = 0.0
    final: float = 0.0               # 지금은 rrf 와 같음(개념 일치 가산점 자리)


@dataclass(frozen=True)
class MatchedConcept:
    """질의와 맞은 개념(설계 §4.5). 대표 사규(related_doc_ids)는 권한 밖 문서 ID 가 있어 담지 않습니다."""
    concept_id: str
    canonical_term: str
    synonyms: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class KeywordToken:
    token: str
    source: Literal["query", "canonical", "synonym"]  # 원래 질의어·대표어·동의어
    concept_id: str | None = None


@dataclass(frozen=True)
class Trace:
    """검색 추적(architecture.md 7.2 중 이번 단계 분량)."""
    dict_version: str
    keyword_tokens: tuple[KeywordToken, ...]  # 키워드 채널에 넣은 토큰과 출처
    keyword_hits: int  # 열람 조건을 건 SQL 이 돌려준 수(대표 사규 포함)
    vector_hits: int
    graph_hits: int = 0


@dataclass(frozen=True)
class Status:
    degraded: bool = False
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    matched_concepts: list[MatchedConcept]
    trace: Trace
    status: Status
    # 벡터 채널 1위 청크의 코사인(근거 부족 판정, qa.md §2). 합친 결과 밖으로 밀려도 남도록 따로 둠.
    # 벡터 채널을 안 돌았거나(degraded) 후보가 없으면 None. 점수 원값이라 응답에 내지 않습니다(설계 §2.2)
    vector_top1_cosine: float | None = None


@dataclass(frozen=True)
class DiscoverHint:
    """존재 공개 안내(설계 §3). 협조전 문구는 부르는 쪽이 rules 에서 붙입니다."""
    doc_id: str
    title: str
    dept_name: str  # departments.default_name. 응답의 표시 이름은 부르는 쪽이 활성 팩 라벨로(약속서 00 5절)
    dept_owner: str


@dataclass(frozen=True)
class GraphItem:
    """문서 하나의 ontology_graph_meta. 모양은 그대로이고 세 가지만 다릅니다(4차 회신 3번):
    볼 수 없는 문서를 가리키는 항목은 뺌, is_active 는 현행 포인터로 다시 채움, 항목마다 source_doc_id."""
    source_doc_id: str
    meta: dict
