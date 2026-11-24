"""메모리 온톨로지 사전 (settings.md §2, 경계 계약 2.6).

`reload_if_changed` 를 앱 시작 때와 짧은 주기마다 부릅니다(주기 루프는 앱을 띄울 때 붙임).
Kiwi 사용자 사전에는 팩과 관계없이 모든 개념을 넣습니다(계약 2.4 "전부"). 팩으로 거르는 것은 쓰는 쪽입니다.
"""
from dataclasses import dataclass

from rag_api.shared.tokenize import ConceptTerm, load_dictionary

FINGERPRINT_SQL = "SELECT string_agg(DISTINCT dict_version, ',') || ':' || count(*) FROM ontology_concepts"
CONCEPTS_SQL = """
SELECT concept_id, canonical_term, synonyms, related_doc_ids, description, knowledge_pack, dept_owner
FROM ontology_concepts ORDER BY concept_id
"""


@dataclass(frozen=True)
class Concept:
    concept_id: str
    canonical_term: str
    synonyms: tuple[str, ...]
    related_doc_ids: tuple[str, ...]
    description: str
    knowledge_pack: str
    dept_owner: str | None = None  # 해설의 부서(용어 카드 순서, search.md §4). NULL 이면 전사로 봄


@dataclass(frozen=True)
class OntologyDictionary:
    fingerprint: str | None  # 계약 2.6 지문. 표가 비었으면 None
    concepts: tuple[Concept, ...]


_current: OntologyDictionary | None = None


def current() -> OntologyDictionary:
    if _current is None:
        raise RuntimeError("온톨로지 사전을 아직 올리지 않았습니다")
    return _current


async def reload_if_changed(conn) -> bool:
    """지문을 읽어 올린 사전과 다르면 다시 올리고 True. `conn` 은 asyncpg 연결."""
    global _current
    if _current is not None and await conn.fetchval(FINGERPRINT_SQL) == _current.fingerprint:
        return False
    # 지문과 행을 같은 스냅샷에서 읽음(파이프라인은 사전·재태깅을 한 트랜잭션으로 커밋, 계약 2.6)
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        fingerprint = await conn.fetchval(FINGERPRINT_SQL)
        rows = await conn.fetch(CONCEPTS_SQL)
    concepts = tuple(
        Concept(r["concept_id"], r["canonical_term"], tuple(r["synonyms"]), tuple(r["related_doc_ids"]),
                r["description"], r["knowledge_pack"], r["dept_owner"])
        for r in rows
    )
    dict_version = fingerprint.rsplit(":", 1)[0] if fingerprint else ""
    # ponytail: 사전 교체 때 워커마다 이벤트 루프가 약 1.2초 멈춤(kiwipiepy 가 Kiwi 생성 중 GIL 을 잡음). 교체가 잦아지면 토큰화를 별도 프로세스로.
    load_dictionary([ConceptTerm(c.canonical_term, c.synonyms) for c in concepts], dict_version)
    _current = OntologyDictionary(fingerprint, concepts)
    return True
