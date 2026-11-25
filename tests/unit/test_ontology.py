import asyncio
from contextlib import asynccontextmanager

from rag_api.settings import ontology
from rag_api.shared.tokenize import current_dict_version, tokenize_ko


def row(concept_id, canonical, synonyms, pack="base"):
    return {"concept_id": concept_id, "canonical_term": canonical, "synonyms": synonyms,
            "related_doc_ids": ["XX-FIN-901"], "description": "설명", "dict_version": "v1.0", "knowledge_pack": pack, "dept_owner": None}


class FakeConn:
    """asyncpg 연결 대신. 지문은 ontology.FINGERPRINT_SQL 결과처럼 계산."""

    def __init__(self, rows):
        self.rows = rows
        self.fetches = 0
        self.in_tx = False

    def fingerprint(self):
        return f"{','.join(sorted({r['dict_version'] for r in self.rows}))}:{len(self.rows)}" if self.rows else None

    async def fetchval(self, sql):
        assert sql == ontology.FINGERPRINT_SQL
        return self.fingerprint()

    async def fetch(self, sql):
        assert sql == ontology.CONCEPTS_SQL and self.in_tx
        self.fetches += 1
        return self.rows

    @asynccontextmanager
    async def transaction(self, **kw):
        assert kw == {"isolation": "repeatable_read", "readonly": True}
        self.in_tx = True
        yield
        self.in_tx = False


def test_load_compare_swap():
    ontology._current = None
    conn = FakeConn([row("CON-XX-901", "지출결의서", ["지결", "비용정산"]),
                     row("CON-XX-903", "계약서 사전검토", ["법무검토"]),
                     row("CON-XX-902", "운영 배포 승인", [], pack="it_service")])

    assert asyncio.run(ontology.reload_if_changed(conn)) is True  # 처음 올림
    d = ontology.current()
    assert d.fingerprint == "v1.0:3"
    assert [c.concept_id for c in d.concepts] == ["CON-XX-901", "CON-XX-903", "CON-XX-902"]
    assert d.concepts[0].synonyms == ("지결", "비용정산") and d.concepts[2].knowledge_pack == "it_service"
    assert current_dict_version() == "v1.0"
    # 팩과 관계없이 모든 개념이 Kiwi 사전에 들어감
    assert tokenize_ko("계약서 사전검토와 운영 배포 승인") == ["계약서사전검토", "운영배포승인"]

    assert asyncio.run(ontology.reload_if_changed(conn)) is False  # 지문 같음 → 그대로
    assert conn.fetches == 1 and ontology.current() is d

    conn.rows = conn.rows + [row("CON-XX-905", "근태·휴가", ["반차"])]  # 지문 바뀜 → 다시 올림
    assert asyncio.run(ontology.reload_if_changed(conn)) is True
    assert ontology.current().fingerprint == "v1.0:4" and conn.fetches == 2
    assert tokenize_ko("근태·휴가 근태휴가") == ["근태휴가", "근태휴가"]
