import asyncio
from collections import Counter
from dataclasses import fields
from datetime import datetime

import pytest
from conftest import DOCS, FakeEmbedder, chunk_embed_text, fake_vec

from rag_api.auth.context import AuthContext
from rag_api.knowledge_engine import channels, term_stats
from rag_api.knowledge_engine.acl import acl_predicate, active_packs
from rag_api.knowledge_engine.engine import KnowledgeEngine
from rag_api.knowledge_engine.types import DiscoverHint, RetrievalQuery
from rag_api.shared.config import Config
from rag_api.shared.tokenize import tokenize_ko

NOW = datetime(2026, 11, 21)


def user(status, dept, extra=(), role="EMPLOYEE"):
    return AuthContext(principal_type="user", status=status, dept_code=dept, extra_depts=list(extra), role=role,
                       active_pack="it_service", snapshot_at=NOW)


REQUESTERS = {
    "anonymous": AuthContext(principal_type="anonymous", active_pack="it_service", snapshot_at=NOW),
    "pending": user("PENDING", "HR"),
    "revision": user("REVISION_REQ", "LGL"),
    "rejected": user("REJECTED", "FIN"),
    "hr": user("ACTIVE", "HR"),
    "fin_hr": user("ACTIVE", "FIN", extra=["HR"]),
    "admin": user("ACTIVE", "COMMON", role="ADMIN"),
    "system": AuthContext(principal_type="system", active_pack="it_service", snapshot_at=NOW),
}
# 계획 3.3 표를 이 데이터로 적은 것. 폐기·검수 대기·포인터 어긋남·ecommerce·benchmark 문서는 어디에도 없음
PUBLIC = {"T-PUB", "T-REV", "T-PUB-SAL", "T-EX"}
ALL_EMP = PUBLIC | {"T-ALL", "T-ITS"}
VISIBLE = {
    "anonymous": PUBLIC, "pending": PUBLIC, "revision": PUBLIC, "rejected": PUBLIC, "system": ALL_EMP,
    "hr": ALL_EMP | {"T-HR", "T-HR-SAL"},
    "fin_hr": ALL_EMP | {"T-HR", "T-HR-SAL", "T-FIN"},
    "admin": ALL_EMP | {"T-HR", "T-HR-SAL", "T-FIN", "T-LGL"},
}
CURRENT_CHUNKS = {  # 문서 → {현행 버전 청크 ID: (title, section_path, content)}
    d["doc_id"]: {f"{d['doc_id']}::{v['tag']}::c{i:03d}": (d["title"], f"제{i}조", text)
                  for i, text in enumerate(v["chunks"], 1)}
    for d in DOCS for v in d["versions"] if v.get("current")
}


def visible_chunks(doc_ids):
    return {cid: src for doc in doc_ids for cid, src in CURRENT_CHUNKS[doc].items()}


def cfg(**kw):
    return Config(database_url="unused", **kw)


@pytest.mark.parametrize("who", REQUESTERS)
@pytest.mark.parametrize("include_benchmark", [False, True])
def test_no_chunk_outside_acl(with_pool, who, include_benchmark):
    ctx = REQUESTERS[who]
    docs = VISIBLE[who] | ({"T-BENCH"} if include_benchmark else set())
    allowed = visible_chunks(docs)
    where = acl_predicate(ctx, active_packs(ctx, include_benchmark))
    query = "출장비 정산 기준"

    async def run(pool):
        kw = await channels.keyword(pool, where, " ".join(tokenize_ko(query)), 100)
        vec = await channels.vector(pool, where, fake_vec(query), 100)
        res = await KnowledgeEngine(pool, FakeEmbedder(), cfg(result_top_n=100)).retrieve(
            RetrievalQuery(query, include_benchmark=include_benchmark), ctx)
        return kw, vec, res

    kw, vec, res = with_pool(run)
    # 키워드(토큰 OR): 볼 수 있고 토큰이 하나라도 맞는 현행 청크가 모두, 그 밖은 0건(ACTIVE 가 PUBLIC 을 보는 것 포함)
    qtok = set(tokenize_ko(query))
    assert {r["chunk_id"] for r in kw} == {cid for cid, src in allowed.items() if qtok & set(tokenize_ko("\n".join(src)))}
    # 벡터: 상한이 커서 볼 수 있는 현행 청크 전부, 그 밖은 0건
    assert {r["chunk_id"] for r in vec} == set(allowed)
    assert {c.chunk_id for c in res.chunks} <= set(allowed) and res.chunks
    assert not res.status.degraded


@pytest.mark.parametrize("who", REQUESTERS)
def test_related_docs_respect_acl_and_pick_best_chunk(with_pool, who):
    """확장을 켜면 개념 CON-T-001 의 대표 사규(권한 밖·폐기·다른 팩·없는 문서 포함)가 대표 청크로 후보에 들어감.
    키워드 상한을 0 으로 둬 모든 대표 사규가 대표 청크 경로를 지나게 하고, 벡터는 끔."""
    ctx = REQUESTERS[who]
    query = "여비규정 접속 코딩"  # 접속 → T-EX 제4조, 코딩 → T-PUB-SAL 제2조에만 있음
    qtok = set(tokenize_ko(query))

    def best(doc):  # 그 문서에서 토큰이 맞는 청크(이 데이터에서는 많아야 하나), 없으면 첫 청크
        chunks = CURRENT_CHUNKS[doc]
        return next((cid for cid, src in chunks.items() if qtok & set(tokenize_ko("\n".join(src)))), next(iter(chunks)))

    async def run(pool):
        config = cfg(ontology_expansion_enabled=True, keyword_top_k=0, vector_channel_enabled=False, result_top_n=100)
        return await KnowledgeEngine(pool, FakeEmbedder(), config).retrieve(RetrievalQuery(query), ctx)

    res = with_pool(run)
    assert [c.concept_id for c in res.matched_concepts] == ["CON-T-001"]
    assert ("출장여비", "synonym") in [(t.token, t.source) for t in res.trace.keyword_tokens]
    assert {c.chunk_id for c in res.chunks} == {best(doc) for doc in VISIBLE[who]}  # 볼 수 있는 문서마다 하나, 그 밖은 0건
    for c in res.chunks:
        assert (c.bm25 is not None) == bool(qtok & set(c.lexemes))  # 토큰이 안 맞는 대표 청크는 bm25 None
    assert {c.chunk_id for c in res.chunks if c.bm25} == {"T-EX::v1.0::c004", "T-PUB-SAL::v1.0::c002"}  # 첫 청크가 아님


def test_narrow_scope_still_fills_top_k(with_pool):
    """권한 밖 문서(T-HR-SAL)가 더 잘 맞아도 열람 범위가 좁은 요청자의 상위 K 가 채워짐."""
    query = "연봉 밴드 기준 연봉 밴드 표"

    async def top3(pool, ctx):
        where = acl_predicate(ctx, active_packs(ctx, False))
        kw = await channels.keyword(pool, where, " ".join(tokenize_ko(query)), 3)
        vec = await channels.vector(pool, where, fake_vec(query), 3)
        return [r["doc_id"] for r in kw], [r["doc_id"] for r in vec]

    async def run(pool):
        return await top3(pool, REQUESTERS["hr"]), await top3(pool, REQUESTERS["anonymous"])

    (hr_kw, hr_vec), (anon_kw, anon_vec) = with_pool(run)
    assert hr_kw == ["T-HR-SAL"] * 3 and hr_vec == ["T-HR-SAL"] * 3  # 볼 수 있으면 이 문서가 더 잘 맞음
    assert anon_kw == ["T-PUB-SAL"] * 3
    assert len(anon_vec) == 3 and set(anon_vec) <= PUBLIC


def test_tsv_roundtrip(with_pool):
    async def run(pool):
        return await pool.fetchrow("""
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE to_tsvector('simple', tsv_to_text(tsv)) <> tsv) AS mismatch,
                   count(*) FILTER (WHERE tsv_to_text(tsv) = '') AS no_position
            FROM chunks""")

    r = with_pool(run)
    assert r["n"] == sum(len(v["chunks"]) for d in DOCS for v in d["versions"])  # 2.5 예문(T-EX) 포함
    assert (r["mismatch"], r["no_position"]) == (0, 0)


def test_halfvec_text_argument(with_pool):
    """'[...]' 글자 인자가 asyncpg 로 넘어가 halfvec 로 바뀜(R1-12)."""
    doc = next(d for d in DOCS if d["doc_id"] == "T-ALL")
    text = chunk_embed_text(doc, "제1조", doc["versions"][0]["chunks"][0])

    async def run(pool):
        dist = await pool.fetchval("SELECT embedding <=> $1::halfvec FROM chunks WHERE chunk_id = 'T-ALL::v1.0::c001'",
                                   str(fake_vec(text)))
        ctx = REQUESTERS["system"]
        top = await channels.vector(pool, acl_predicate(ctx, active_packs(ctx, False)), fake_vec(text), 1)
        return dist, top

    dist, top = with_pool(run)
    assert dist < 1e-3  # halfvec 정밀도
    assert top[0]["chunk_id"] == "T-ALL::v1.0::c001" and 1 - top[0]["distance"] > 0.999


def test_embedding_failure_degrades_to_keyword(with_pool):
    async def run(pool):
        eng = KnowledgeEngine(pool, FakeEmbedder(fail=ConnectionError("down")), cfg())
        return await eng.retrieve(RetrievalQuery("출장비 정산 기준"), REQUESTERS["hr"])

    res = with_pool(run)
    assert res.status.degraded and res.status.reason_codes == ("EMBEDDING_UNAVAILABLE",)
    assert res.chunks and all(c.cosine is None and c.bm25 is not None for c in res.chunks)
    assert {c.doc.doc_id for c in res.chunks} <= VISIBLE["hr"]


def test_dictionary_loaded_from_db(test_dsn):
    # init.sql 시드 사전(계약서 사전검토)이 DB 에서 올라와 띄어쓰기 든 용어를 붙임
    assert tokenize_ko("계약서 사전검토 요청") == ["계약서사전검토", "요청"]


# --- 3단계: 협업 그래프·그래프 채널·존재 공개 안내·검색 통계 ---
ACTIVE_DOCS = {d["doc_id"] for d in DOCS for v in d["versions"] if v.get("current") and v["status"] == "active"}
NOT_CURRENT_PUBLIC = {"T-RETIRED", "T-PENDING", "T-BADPTR"}  # PUBLIC·base 인데 현행 아님: 볼 수 있어 그래프에 is_active=false 로 남음
GRAPHS = {d["doc_id"]: d["graph"] for d in DOCS if "graph" in d}
OWNER = {d["doc_id"]: d["dept_owner"] for d in DOCS}  # 준비 데이터의 소관 부서(구현과 따로 적은 표)


def steps(item, key):
    return [(s["doc_id"], s["is_active"]) for s in item.meta[key]]


@pytest.mark.parametrize("who", REQUESTERS)
def test_procedure_graph_drops_invisible_keeps_retired(with_pool, who):
    seeable = VISIBLE[who] | NOT_CURRENT_PUBLIC
    items = with_pool(lambda pool: KnowledgeEngine(pool, FakeEmbedder(), cfg()).procedure_graph(
        ["T-PUB", "T-ALL", "T-HR"], REQUESTERS[who]))
    assert [i.source_doc_id for i in items] == [d for d in ("T-PUB", "T-ALL") if d in VISIBLE[who]]  # T-HR 은 그래프 없음
    for item in items:
        g = GRAPHS[item.source_doc_id]
        for key in ("prev_steps", "next_steps"):  # 볼 수 있는 단계만, 서식은 볼 수 있는 현행일 때만 남음
            assert item.meta[key] == [
                {**{k: v for k, v in s.items() if k != "required_form_id" or v in VISIBLE[who]},
                 "is_active": s["doc_id"] in ACTIVE_DOCS, "source_doc_id": item.source_doc_id}
                for s in g[key] if s["doc_id"] in seeable]
        assert item.meta["conflicts"] == [  # 상대가 볼 수 있는 현행 문서일 때만. 상대의 소관 부서를 더함(qa R2)
            {**c, "source_doc_id": item.source_doc_id, "target_dept_owner": OWNER[c["target_doc_id"]]}
            for c in g["conflicts"] if c["target_doc_id"] in VISIBLE[who]]
        assert (item.meta["workflow_name"], item.meta["current_step"]) == (g["workflow_name"], g["current_step"])
    by_src = {i.source_doc_id: i for i in items}
    forms = lambda item, key: [(s["doc_id"], s.get("required_form_id")) for s in item.meta[key]]
    if who == "anonymous":  # 위 식이 맞는지 손으로 적은 값으로도 봄
        assert steps(by_src["T-PUB"], "next_steps") == [("T-REV", True), ("T-RETIRED", False)]
        # 폐기 서식(T-RETIRED)은 빠짐. T-EX 는 서식으로만 가리켜 조회에 서식 ID 가 들어가야 남음
        assert forms(by_src["T-PUB"], "next_steps") == [("T-REV", None), ("T-RETIRED", "T-EX")]
        assert by_src["T-PUB"].meta["conflicts"] == []  # T-HR 볼 수 없음, T-PENDING 현행 아님
    if who == "system":
        assert forms(by_src["T-PUB"], "next_steps")[0] == ("T-ALL", None)  # 서식 T-HR 볼 수 없음
    if who == "hr":
        assert forms(by_src["T-PUB"], "next_steps")[0] == ("T-ALL", "T-HR")
        assert [c["target_doc_id"] for c in by_src["T-PUB"].meta["conflicts"]] == ["T-HR"]
    if who == "admin":
        assert steps(by_src["T-ALL"], "prev_steps") == [("T-PUB", True), ("T-HR", True)]
        assert steps(by_src["T-ALL"], "next_steps") == [("T-FIN", True), ("T-BADPTR", False), ("T-PENDING", False)]
        assert "required_form_id" not in by_src["T-ALL"].meta["next_steps"][0]  # STD-FIN-022 는 없는 문서
        assert [c["target_doc_id"] for c in by_src["T-ALL"].meta["conflicts"]] == ["T-LGL", "T-REV"]  # 폐기·검수 대기 빠짐


@pytest.mark.parametrize("who", REQUESTERS)
def test_graph_channel_adds_seed_and_only_visible_neighbors(with_pool, who):
    """셋째 목록 = 상위 문서(T-PUB) 자신, 그 뒤에 앞뒤 단계(T-ALL·T-REV·T-RETIRED) 중 볼 수 있는 현행 문서만(단계 순)."""
    res = with_pool(lambda pool: KnowledgeEngine(pool, FakeEmbedder(), cfg(graph_seed_docs=1, result_top_n=100)).retrieve(
        RetrievalQuery("회사 소개", strategy="graph_hybrid"), REQUESTERS[who]))
    expected = ["T-PUB", *(d for d in ("T-ALL", "T-REV", "T-RETIRED") if d in VISIBLE[who])]
    ranked = sorted((c for c in res.chunks if c.graph_rank), key=lambda c: c.graph_rank)
    assert [c.doc.doc_id for c in ranked] == expected and res.trace.graph_hits == len(expected)
    assert {c.chunk_id for c in res.chunks} <= set(visible_chunks(VISIBLE[who]))


@pytest.mark.parametrize("who", REQUESTERS)
def test_discoverable_hints_per_requester(with_pool, who):
    """요청자가 볼 수 없는(열람 집합과 겹치지 않는) 공개 표시·현행·활성 팩 문서만, ACTIVE 비ADMIN 에게만.
    hr: T-FIN 만. hr 가 볼 수 없는 T-LGL-ECO(다른 팩)·T-LGL-OLD(폐기, 포인터 NULL)·T-LGL-BAD(포인터가 archived)는
    팩·현행 조건으로 빠짐. T-ECO·T-RETIRED·T-PUB 은 ALL_EMP·PUBLIC 이라 hr 가 볼 수 있는 열람 대상이어서 애초에 대상 아님."""
    hints = with_pool(lambda pool: KnowledgeEngine(pool, FakeEmbedder(), cfg()).discoverable_hints(
        "인사 규정 문의", REQUESTERS[who]))
    assert [h.doc_id for h in hints] == (["T-FIN"] if who == "hr" else [])


def test_discoverable_hints_order_limit_and_fields(with_pool):
    lgl = user("ACTIVE", "LGL")
    hints = lambda pool, n: KnowledgeEngine(pool, FakeEmbedder(), cfg(discover_hint_limit=n)).discoverable_hints(
        "인사 규정 문의", lgl)
    three, one = with_pool(lambda pool: asyncio.gather(hints(pool, 3), hints(pool, 1)))
    assert three == [DiscoverHint("T-HR", "인사 규정", "인사·노무", "HR"), DiscoverHint("T-FIN", "재무 규정", "재무·회계·세무", "FIN")]
    assert one == three[:1]  # 겹친 토큰 수(2, 1) 순
    assert [f.name for f in fields(DiscoverHint)] == ["doc_id", "title", "dept_name", "dept_owner"]  # 본문·청크 없음


def test_term_stats_match_fixture(with_pool):
    term_stats._current = None

    async def run(pool):
        async with pool.acquire() as conn:
            await term_stats.refresh(conn)

    with_pool(run)
    st = term_stats.current()
    current = [src for d in ACTIVE_DOCS for src in CURRENT_CHUNKS[d].values()]  # 팩·열람과 무관한 현행 청크 전부
    df = Counter(t for src in current for t in set(tokenize_ko("\n".join(src))))
    assert st.n == len(current) == 27  # 1×10(T-LGL-ECO 포함) + T-HR-SAL 5 + T-PUB-SAL 4 + T-EX 8
    assert st.df == dict(df)
