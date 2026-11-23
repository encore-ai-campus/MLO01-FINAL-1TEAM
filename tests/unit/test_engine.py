import asyncio
import math
import time
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest

from rag_api.auth.context import AuthContext
from rag_api.knowledge_engine import channels, engine
from rag_api.knowledge_engine.acl import Sql
from rag_api.knowledge_engine.engine import KnowledgeEngine, expand, filter_graph, fuse
from rag_api.knowledge_engine.types import DiscoverHint, GraphItem, KeywordToken, RetrievalQuery
from rag_api.settings import ontology
from rag_api.settings.ontology import Concept, OntologyDictionary
from rag_api.shared.config import Config
from rag_api.shared.tokenize import ConceptTerm, load_dictionary

CONCEPTS = (
    Concept("CON-FIN-001", "지출결의서", ("지결", "비용정산"), ("IT-FIN-008", "STD-FIN-022"), "설명", "base"),
    Concept("CON-ENG-001", "운영 배포 승인", ("릴리즈승인",), ("IT-ENG-020",), "설명", "it_service"),
    Concept("CON-ECO-001", "반품 처리", ("환불처리",), ("EC-001",), "설명", "ecommerce"),
)
HR = AuthContext(principal_type="user", status="ACTIVE", dept_code="HR", role="EMPLOYEE", active_pack="it_service",
                 snapshot_at=datetime(2026, 11, 21))


def use_dictionary(concepts, fingerprint):
    load_dictionary([ConceptTerm(c.canonical_term, c.synonyms) for c in concepts], "v1.0")
    ontology._current = OntologyDictionary(fingerprint, tuple(concepts))


@pytest.fixture(autouse=True)
def dictionary():
    engine._term_tokens.cache_clear()
    use_dictionary(CONCEPTS, "v1.0:3")


def row(chunk_id, doc_id="D1", tier=1, lexemes=("지결",), **score):
    return {"chunk_id": chunk_id, "section_path": "제1조", "content": "본문", "lexemes": list(lexemes), "doc_id": doc_id,
            "title": "제목", "version_tag": "v1.0", "doc_type": "official_policy", "source_tier": tier,
            "dept_owner": "FIN", "effective_from": date(2026, 1, 1), "version_id": "ver-" + doc_id, **score}


def cfg(**kw):
    return Config(database_url="x", **kw)


def test_fuse_rrf_merge_and_weight():
    kw_rows = [row("A", tier=2, bm25=3.0), row("B", lexemes=("지결", "정산"), bm25=2.0)]
    vec_rows = [row("B", distance=0.1), row("C", distance=0.3)]
    out = fuse(kw_rows, vec_rows, {"지결", "정산", "기한"}, cfg())
    assert [c.chunk_id for c in out] == ["B", "C", "A"]
    b, c, a = out
    assert math.isclose(b.rrf, 1 / 62 + 1 / 61) and math.isclose(b.final, b.rrf * 1.2)  # 같은 청크는 하나로
    assert (b.bm25, b.keyword_rank, b.matched_tokens) == (2.0, 2, 2) and math.isclose(b.cosine, 0.9)
    assert math.isclose(c.final, 1 / 62 * 1.2) and c.bm25 is None and c.keyword_rank is None
    assert math.isclose(a.final, 1 / 61 * 1.0) and a.cosine is None  # source_tier 2 는 가중 없음
    assert [c.chunk_id for c in fuse(kw_rows, vec_rows, set(), cfg(result_top_n=2, rrf_k=1))] == ["B", "A"]
    b, c, a = fuse(kw_rows, vec_rows, set(), cfg(domain_weight_tier1=1.5))  # DOMAIN_WEIGHT_TIER1 을 씀
    assert math.isclose(b.final, b.rrf * 1.5) and math.isclose(a.final, a.rrf)


def test_expand_off_matches_concept_only():
    matched, kw, related = expand(["지결", "때"], list(CONCEPTS), "v1.0:3", False, 20)
    assert [c.concept_id for c in matched] == ["CON-FIN-001"]
    assert kw == [KeywordToken("지결", "query"), KeywordToken("때", "query")] and related == []


def test_expand_on_adds_synonyms_with_source_and_cap():
    _, kw, related = expand(["지결", "때"], list(CONCEPTS), "v1.0:3", True, 20)
    assert kw[2:] == [KeywordToken("지출결의서", "canonical", "CON-FIN-001"),
                      KeywordToken("비용정산", "synonym", "CON-FIN-001")]  # 지결은 이미 질의에 있음
    assert related == ["IT-FIN-008", "STD-FIN-022"]
    _, kw, _ = expand(["지결"], list(CONCEPTS), "v1.0:3", True, 1)
    assert [t.token for t in kw] == ["지결", "지출결의서"]


def test_spaced_term_concept_matches():
    matched, _, _ = expand(["운영배포승인", "절차"], list(CONCEPTS), "v1.0:3", False, 20)
    assert [c.concept_id for c in matched] == ["CON-ENG-001"]


class FakeChannels:
    """channels 의 함수를 가짜로 바꿈. 이름 → 돌려줄 값, 부른 (열람 조각, 나머지 인자)를 calls 에."""

    def __init__(self, monkeypatch, keyword_rows=(), vector_rows=(), rep_rows=(), **others):
        self.calls = {}
        returns = {"keyword": list(keyword_rows), "vector": list(vector_rows), "representative": list(rep_rows),
                   "graph_meta": {}, "graph_targets": ({}, {}), "discoverable": [], **others}
        for name, value in returns.items():
            monkeypatch.setattr(channels, name, self._fake(name, value))

    def _fake(self, name, value):
        async def f(pool, where, *args):
            self.calls[name] = (where, args)
            return value
        return f


class Embedder:
    def __init__(self, fail=None, sleep=0.0):
        self.fail, self.sleep, self.texts = fail, sleep, []

    def embed_query(self, text):
        self.texts.append(text)
        time.sleep(self.sleep)
        if self.fail:
            raise self.fail
        return [1.0] + [0.0] * 1023

    def embed_passages(self, texts):
        raise AssertionError


def run(eng, q, ctx=HR):
    return asyncio.run(eng.retrieve(q, ctx))


def test_retrieve_hybrid(monkeypatch):
    ch = FakeChannels(monkeypatch, [row("A", bm25=1.0)], [row("B", distance=0.2)])
    emb = Embedder()
    res = run(KnowledgeEngine(None, emb, cfg()), RetrievalQuery("지결 올릴 때 영수증은?", dept_filter=("FIN",),
                                                                 doc_types=("official_policy",)))
    assert emb.texts == ["지결 올릴 때 영수증은?"]  # 벡터에는 원래 질의
    where, args = ch.calls["keyword"]
    assert args == ("지결 때 영수증", 20)
    assert where.text.endswith(" AND d.dept_owner = ANY($?::text[]) AND d.doc_type = ANY($?::text[])")
    assert where.args[-2:] == (["FIN"], ["official_policy"])
    assert ch.calls["vector"][0] == where  # 두 채널이 같은 조건
    assert [c.chunk_id for c in res.chunks] == ["A", "B"] and not res.status.degraded
    assert [c.concept_id for c in res.matched_concepts] == ["CON-FIN-001"]
    assert (res.trace.keyword_hits, res.trace.vector_hits, res.trace.dict_version) == (1, 1, "v1.0")
    assert "representative" not in ch.calls  # 확장 꺼짐


@pytest.mark.parametrize("embedder, config, msg", [
    (Embedder(fail=RuntimeError("down")), cfg(), "RuntimeError: down"),
    (Embedder(sleep=0.3), cfg(embed_timeout_s=0.05), "TimeoutError: EMBED_TIMEOUT_S 0.05초 초과"),
    (Embedder(), cfg(vector_channel_enabled=False), None),
])
def test_retrieve_degraded(monkeypatch, caplog, embedder, config, msg):
    ch = FakeChannels(monkeypatch, [row("A", bm25=1.0)], [row("B", distance=0.2)])
    res = run(KnowledgeEngine(None, embedder, config), RetrievalQuery("지결 기한"))
    assert res.status.degraded and res.status.reason_codes == ("EMBEDDING_UNAVAILABLE",)
    assert "vector" not in ch.calls and [c.chunk_id for c in res.chunks] == ["A"]
    if not config.vector_channel_enabled:
        assert embedder.texts == []
    assert all(r.exc_info is None for r in caplog.records)  # 요청마다 traceback 을 남기지 않음
    assert [r.getMessage() for r in caplog.records] == ([f"임베딩 실패, 키워드 채널만 씀: {msg}"] if msg else [])


def test_vector_top1_cosine_kept_outside_results(monkeypatch):
    """결과 상한 밖으로 밀린 벡터 1위도 근거 부족 판정용 코사인으로 넘어감(R4-03)."""
    kw_rows = [row("A", bm25=2.0), row("C", bm25=1.0)]
    FakeChannels(monkeypatch, kw_rows, [row("B", distance=0.1), row("C", distance=0.4)])
    res = run(KnowledgeEngine(None, Embedder(), cfg(result_top_n=1)), RetrievalQuery("지결 기한"))
    assert [c.chunk_id for c in res.chunks] == ["C"]  # 벡터 1위 B 는 결과에 없음
    assert math.isclose(res.vector_top1_cosine, 0.9)
    FakeChannels(monkeypatch, kw_rows, [row("B", distance=0.1)])
    assert run(KnowledgeEngine(None, Embedder(), cfg(vector_channel_enabled=False)), RetrievalQuery("지결")).vector_top1_cosine is None
    FakeChannels(monkeypatch, kw_rows, [])
    assert run(KnowledgeEngine(None, Embedder(), cfg()), RetrievalQuery("지결")).vector_top1_cosine is None  # 후보 없음


def test_retrieve_expansion_on_adds_related_docs(monkeypatch):
    ch = FakeChannels(monkeypatch, [row("A", doc_id="IT-FIN-008", bm25=1.0)], [],
                      [row("R", doc_id="STD-FIN-022", bm25=0.0)])
    res = run(KnowledgeEngine(None, Embedder(), cfg(ontology_expansion_enabled=True)),
              RetrievalQuery("지결 환불처리 기한"))  # 환불처리는 ecommerce 팩 개념이라 활성 팩(it_service)에서 안 맞음
    assert ch.calls["keyword"][1][0] == "지결 환불처리 기한 지출결의서 비용정산"
    assert ch.calls["representative"][1] == (["STD-FIN-022"], ch.calls["keyword"][1][0])  # 이미 찾은 문서는 뺌
    assert [c.concept_id for c in res.matched_concepts] == ["CON-FIN-001"]
    assert not hasattr(res.matched_concepts[0], "related_doc_ids")  # 권한 밖 문서 ID 를 결과에 싣지 않음
    r = next(c for c in res.chunks if c.chunk_id == "R")
    assert r.keyword_rank == 2  # 키워드 목록 끝에 붙음
    assert [t.source for t in res.trace.keyword_tokens] == ["query"] * 3 + ["canonical", "synonym"]


def test_title_tokens_retokenized_when_fingerprint_changes(monkeypatch):
    """같은 판(v1.0)에서 행만 늘어도(지문 v1.0:3 → v1.0:4) 존재 공개 안내가 제목을 다시 잘라 맞춤(R2-03).
    사전 용어는 저마다 사용자 단어라 다른 행이 늘어도 토큰이 안 바뀝니다(시드 73개 실측 0건). 사전 밖 글인
    문서 제목은 행이 늘면 토큰이 바뀌므로 그 경우로 봅니다(문서 제목 179개 전체에 시드 개념 12개를 하나씩 빼 보면
    바뀌는 제목 수의 합이 21, 개념마다 0~5)."""
    title = "부서 간 자료 요청 협조전 작성 지침"
    FakeChannels(monkeypatch, discoverable=[{"doc_id": "K", "title": title, "dept_name": "인프라", "dept_owner": "INF"}])
    hints = lambda: [h.doc_id for h in asyncio.run(KnowledgeEngine(None, Embedder(), cfg()).discoverable_hints("협조전", HR))]
    assert hints() == ["K"]  # 질의·제목 모두 협조·전
    use_dictionary(CONCEPTS + (Concept("CON-COM-002", "협조전", ("업무협조전",), (), "설명", "base"),), "v1.0:4")
    assert hints() == ["K"]  # 질의가 협조전 한 토큰. 제목도 다시 잘라야 맞음


class FakePool:
    """`pool.acquire()`·`conn.transaction()`·`execute`·`fetch` 만 흉내 냄."""

    def __init__(self, rows):
        self.rows, self.executed = rows, []

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self, **kw):
        yield

    async def execute(self, sql):
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        return self.rows


def test_vector_channel_resorts_by_distance():
    pool = FakePool([{"chunk_id": c, "distance": d} for c, d in [("B", 0.3), ("A", 0.1), ("C", 0.2)]])
    rows = asyncio.run(channels.vector(pool, Sql("TRUE"), [1.0] + [0.0] * 1023, 3))
    assert [r["chunk_id"] for r in rows] == ["A", "C", "B"]  # relaxed_order 로 어긋난 순서를 앱에서 바로잡음
    assert pool.executed == ["SET LOCAL hnsw.iterative_scan = 'relaxed_order'"]


def test_channels_break_score_ties_by_chunk_id():
    """점수가 같으면 DB 가 준 물리 순서와 상관없이 chunk_id 순(다시 적재해도 같은 순위)."""
    kw = FakePool([{"chunk_id": c, "bm25": s} for c, s in [("C", 2.0), ("B", 2.0), ("D", 3.0), ("A", 1.0)]])
    assert [r["chunk_id"] for r in asyncio.run(channels.keyword(kw, Sql("TRUE"), "지결", 4))] == ["D", "B", "C", "A"]
    vec = FakePool([{"chunk_id": c, "distance": d} for c, d in [("C", 0.2), ("B", 0.2), ("A", 0.3)]])
    assert [r["chunk_id"] for r in asyncio.run(channels.vector(vec, Sql("TRUE"), [1.0] + [0.0] * 1023, 3))] == ["B", "C", "A"]


GRAPH_META = {
    "workflow_name": "W", "current_step": 2,
    "prev_steps": [{"step": 1, "doc_id": "A", "title": "a", "dept_code": "HR", "required_form_id": "OLD", "is_active": False}],
    "next_steps": [{"step": 3, "doc_id": "B", "title": "b", "dept_code": "FIN", "required_form_id": "F", "is_active": True},
                   {"step": 4, "doc_id": "HIDDEN", "title": "h", "dept_code": "LGL", "is_active": True}],
    "conflicts": [{"target_doc_id": "A", "target_title": "a", "clause_pair": "x", "priority_rule": "y"},
                  {"target_doc_id": "B", "target_title": "b", "clause_pair": "x", "priority_rule": "y"},
                  {"target_doc_id": "HIDDEN", "target_title": "h", "clause_pair": "x", "priority_rule": "y"}],
}
VISIBLE = {"A": True, "B": False, "F": True, "OLD": False}  # 볼 수 있는 문서 → 현행 여부. HIDDEN 은 볼 수 없음
OWNERS = {"A": "HR", "B": "FIN", "F": "OPS", "OLD": "OPS"}  # 볼 수 있는 문서 → 소관 부서


def test_filter_graph_drops_invisible_refills_active_adds_source():
    out = filter_graph("SRC", GRAPH_META, VISIBLE, OWNERS)
    assert out == {
        "workflow_name": "W", "current_step": 2,
        # 서식 OLD 는 볼 수 있지만 현행이 아니어서 키만 빠짐
        "prev_steps": [{"step": 1, "doc_id": "A", "title": "a", "dept_code": "HR", "is_active": True, "source_doc_id": "SRC"}],
        # 단계 B 는 현행이 아니어도 볼 수 있어 남음(is_active=false). 서식 F 는 현행이라 남음
        "next_steps": [{"step": 3, "doc_id": "B", "title": "b", "dept_code": "FIN", "required_form_id": "F",
                        "is_active": False, "source_doc_id": "SRC"}],
        # 충돌은 상대가 현행일 때만: B(폐기)·HIDDEN 은 빠짐. 상대의 소관 부서를 더함(qa R2)
        "conflicts": [{"target_doc_id": "A", "target_title": "a", "clause_pair": "x", "priority_rule": "y",
                       "source_doc_id": "SRC", "target_dept_owner": "HR"}],
    }
    assert GRAPH_META["prev_steps"][0]["is_active"] is False  # 원본은 그대로
    assert filter_graph("SRC", {"workflow_name": "W"}, {}, {}) == {"workflow_name": "W"}  # 없는 키는 만들지 않음


def test_procedure_graph_reads_targets_once_without_current_condition(monkeypatch):
    ch = FakeChannels(monkeypatch, graph_meta={"SRC": GRAPH_META}, graph_targets=(VISIBLE, OWNERS))
    items = asyncio.run(KnowledgeEngine(None, Embedder(), cfg()).procedure_graph(["SRC", "NOPE"], HR))
    assert items == [GraphItem("SRC", filter_graph("SRC", GRAPH_META, VISIBLE, OWNERS))]
    meta_where, (doc_ids,) = ch.calls["graph_meta"]
    assert "current_version_id" in meta_where.text and doc_ids == ["SRC", "NOPE"]  # 요청 문서는 현행·열람 조건
    target_where, (target_ids,) = ch.calls["graph_targets"]
    assert "current_version_id" not in target_where.text  # 현행 조건 없이
    assert target_ids == ["A", "B", "F", "HIDDEN", "OLD"]  # 단계·서식·충돌 상대를 한 번에


TITLES = [{"doc_id": "D1", "title": "급여 지급 규정", "dept_name": "인사·노무", "dept_owner": "HR"},
          {"doc_id": "D2", "title": "지출결의서 작성 규정", "dept_name": "재무·회계·세무", "dept_owner": "FIN"},
          {"doc_id": "D3", "title": "보안 규정", "dept_name": "인프라", "dept_owner": "INF"}]


@pytest.mark.parametrize("who", [
    AuthContext(principal_type="anonymous", active_pack="it_service", snapshot_at=datetime(2026, 11, 21)),
    AuthContext(principal_type="system", active_pack="it_service", snapshot_at=datetime(2026, 11, 21)),
    AuthContext(principal_type="user", status="PENDING", dept_code="HR", active_pack="it_service", snapshot_at=datetime(2026, 11, 21)),
    AuthContext(principal_type="user", status="ACTIVE", role="ADMIN", active_pack="it_service", snapshot_at=datetime(2026, 11, 21)),
])
def test_discoverable_hints_only_for_active_non_admin(monkeypatch, who):
    ch = FakeChannels(monkeypatch, discoverable=TITLES)
    assert asyncio.run(KnowledgeEngine(None, Embedder(), cfg()).discoverable_hints("지출결의서 규정", who)) == []
    assert "discoverable" not in ch.calls


def test_discoverable_hints_title_overlap_order_and_limit(monkeypatch):
    ch = FakeChannels(monkeypatch, discoverable=TITLES)
    hints = asyncio.run(KnowledgeEngine(None, Embedder(), cfg(discover_hint_limit=2)).discoverable_hints("지출결의서 작성 규정", HR))
    assert hints == [DiscoverHint("D2", "지출결의서 작성 규정", "재무·회계·세무", "FIN"), DiscoverHint("D1", "급여 지급 규정", "인사·노무", "HR")]
    assert "NOT (d.allowed_depts" in ch.calls["discoverable"][0].text  # acl.py 의 hidden_predicate


def test_discoverable_hints_widen_with_synonyms_even_when_expansion_off(monkeypatch):
    FakeChannels(monkeypatch, discoverable=TITLES)
    hints = lambda on, cap=20: asyncio.run(KnowledgeEngine(None, Embedder(), cfg(ontology_expansion_enabled=on,
                                                                                  ontology_expansion_max_tokens=cap))
                                           .discoverable_hints("지결 기한", HR))
    assert [h.doc_id for h in hints(False)] == [h.doc_id for h in hints(True)] == ["D2"]  # 지결 → 지출결의서(스위치와 무관)
    assert hints(False, cap=0) == []  # 넓히는 토큰 수 상한은 ONTOLOGY_EXPANSION_MAX_TOKENS


def test_doc_ids_reach_every_channel(monkeypatch):
    """고른 문서(qa 5-13)는 공용 조건이라 키워드·벡터·그래프 출처·대표 청크 모두에 걸림."""
    ch = FakeChannels(monkeypatch, [row("A", doc_id="D1", bm25=2.0)], [row("A", doc_id="D1", distance=0.1)],
                      rep_rows=[row("n", doc_id="N")], graph_meta={"D1": {"next_steps": [{"doc_id": "N"}]}})
    run(KnowledgeEngine(None, Embedder(), cfg()), RetrievalQuery("지결 기한", strategy="graph_hybrid", doc_ids=("D1", "N")))
    for name in ("keyword", "vector", "graph_meta", "representative"):
        where = ch.calls[name][0]
        assert "d.doc_id = ANY($?::text[])" in where.text and ["D1", "N"] in where.args, name


def test_graph_channel_adds_seeds_then_neighbors_as_third_list(monkeypatch):
    metas = {"D1": {"prev_steps": [{"doc_id": "P"}], "next_steps": [{"doc_id": "N"}, {"doc_id": "D2"}]},
             "D2": {"prev_steps": [{"doc_id": "D1"}], "next_steps": [{"doc_id": "N"}, {"doc_id": "M"}]}}
    # SQL 은 doc_id 순으로 줌. 씨앗의 대표 청크는 키워드가 가장 잘 맞는 청크(여기선 A·B)
    reps = [row("A", doc_id="D1"), row("B", doc_id="D2"), row("m", doc_id="M"), row("n", doc_id="N"), row("p", doc_id="P")]
    ch = FakeChannels(monkeypatch, [row("A", doc_id="D1", bm25=2.0), row("B", doc_id="D2", bm25=1.0)], [],
                      rep_rows=reps, graph_meta=metas)
    eng = KnowledgeEngine(None, Embedder(), cfg(vector_channel_enabled=False, graph_seed_docs=2))
    res = run(eng, RetrievalQuery("지결 기한", strategy="graph_hybrid"))
    assert ch.calls["graph_meta"][1] == (["D1", "D2"],)  # 키워드·벡터 상위 문서
    # 씨앗(상위 문서 순) 다음 이웃(씨앗 순, 앞 → 뒤 단계). 씨앗끼리 이웃인 D1·D2 와 겹친 N 은 한 번만
    assert ch.calls["representative"][1][0] == ["D1", "D2", "P", "N", "M"]
    assert ch.calls["representative"][0] is ch.calls["keyword"][0]  # 같은 공용 조건
    by_id = {c.chunk_id: c for c in res.chunks}
    assert [(i, by_id[i].graph_rank) for i in "ABpnm"] == [("A", 1), ("B", 2), ("p", 3), ("n", 4), ("m", 5)]
    assert math.isclose(by_id["A"].rrf, 2 / 61) and math.isclose(by_id["p"].rrf, 1 / 63) and res.trace.graph_hits == 5
    assert [c.chunk_id for c in res.chunks][:2] == ["A", "B"]  # 씨앗이 이웃에 밀리지 않음

    ch = FakeChannels(monkeypatch, [row("A", doc_id="D1", bm25=2.0)], [], rep_rows=reps, graph_meta=metas)
    res = run(eng, RetrievalQuery("지결 기한"))  # hybrid 는 그래프를 읽지 않음
    assert "graph_meta" not in ch.calls and res.trace.graph_hits == 0
