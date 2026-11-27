"""낡은 문서 배지 `unverified`(경계 계약 4.11)를 HTTP 로: 검색·답변 근거·원문 보기가 같은 판정(확인 시각·게시 시각 중 늦은 쪽 + 점검 주기,
긴 주기는 현행 판 머리말의 장). 시험 문서 T-ALL(ALL_EMP)의 확인 시각·현행 판 게시 시각·머리말·시행일만 바꾸고 되돌림."""
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta

from requester import headers
from test_api_qa import ask
from test_api_search import REQUESTERS, _requester, search  # noqa: F401  (autouse 고정물)

from rag_api.shared.current_version import KST


def days_ago(n: int) -> datetime:
    """DB 의 TIMESTAMP 는 한국 벽시계(연결 시간대). 낮 12시로 두어 날짜 경계에서 흔들리지 않게."""
    return datetime.combine(datetime.now(KST).date() - timedelta(days=n), time(12))


@asynccontextmanager
async def aged(conn, doc_id="T-ALL", *, verified=None, published=None, head=None, effective_from=None):
    """현행 판의 게시 시각·시행일·머리말과 문서의 확인 시각을 시험 값으로 바꿈(끝나면 되돌림)."""
    old = await conn.fetchrow("SELECT d.last_verified_at, v.created_at, v.effective_from, v.raw_markdown, v.version_id"
                              " FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id WHERE d.doc_id = $1", doc_id)
    await conn.execute("UPDATE documents SET last_verified_at = $2 WHERE doc_id = $1", doc_id, verified)
    await conn.execute("UPDATE document_versions SET created_at = coalesce($2, created_at), effective_from = coalesce($3, effective_from),"
                       " raw_markdown = coalesce($4, raw_markdown) WHERE version_id = $1", old["version_id"], published, effective_from, head)
    try:
        yield
    finally:
        await conn.execute("UPDATE documents SET last_verified_at = $2 WHERE doc_id = $1", doc_id, old["last_verified_at"])
        await conn.execute("UPDATE document_versions SET created_at = $2, effective_from = $3, raw_markdown = $4 WHERE version_id = $1",
                           old["version_id"], old["created_at"], old["effective_from"], old["raw_markdown"])


async def badges(client, email):
    """같은 문서(T-ALL)의 배지를 검색·답변 근거·원문 보기에서 읽음."""
    found = (await search(client, email, query="회사 규정 출장비 정산 기준")).json()
    hit = next(d for d in found["documents"] if d["doc_id"] == "T-ALL")
    answered = (await ask(client, email, doc_ids=["T-ALL"])).json()
    opened = (await client.get("/api/v1/documents/T-ALL", headers=headers(email))).json()
    assert answered["result"] == "answered"
    return hit["version_badge"], answered["evidence"][0]["version_badge"], opened["version_badge"]


HEAD = "---\ndoc_id: T-ALL\nchapter_id: {}\nallowed_depts: [ALL_EMP]\n---\n# 제목\n"


def test_unverified_in_search_answer_and_open_with_the_same_rule(api):
    async def run(client, conn):
        email = REQUESTERS["hr"][0]
        assert await badges(client, email) == (None, None, None)  # 방금 적재된 문서: 낡지 않음
        async with aged(conn, verified=days_ago(214), published=days_ago(220)):  # 둘 다 6개월보다 오래됨
            assert await badges(client, email) == ("unverified",) * 3
        async with aged(conn, verified=days_ago(10), published=days_ago(400)):  # 최근 확인이 늦은 쪽
            assert await badges(client, email) == (None, None, None)
        async with aged(conn, verified=days_ago(400), published=days_ago(10)):  # 개정(게시)이 늦은 쪽
            assert await badges(client, email) == (None, None, None)
        async with aged(conn, verified=None, published=days_ago(214)):  # 확인 시각 없음 → 게시 시각만
            assert await badges(client, email) == ("unverified",) * 3
        async with aged(conn, verified=days_ago(400), published=days_ago(400), effective_from=datetime.now(KST).date() + timedelta(days=1)):
            assert await badges(client, email) == ("scheduled",) * 3  # 시행 예정이 앞섬
        assert await badges(client, email) == (None, None, None)  # 되돌리면 그대로
    api(run)


def test_long_interval_chapter_reads_the_current_versions_header(api):
    async def run(client, conn):
        email = REQUESTERS["hr"][0]
        for chapter, age, expected in [("ch01", 214, None), ("ch01", 400, "unverified"),  # 정관 장은 12개월
                                       ("ch02", 214, "unverified"), ("ch11", 214, "unverified")]:  # 다른 장은 6개월
            async with aged(conn, verified=days_ago(age), published=days_ago(age), head=HEAD.format(chapter)):
                assert await badges(client, email) == (expected,) * 3, (chapter, age)
        async with aged(conn, verified=days_ago(214), published=days_ago(214), head="머리말 없는 본문"):  # 장을 못 읽으면 짧은 주기
            assert await badges(client, email) == ("unverified",) * 3
        # 공식 사규(official_policy)만: ch01 이어도 다른 종류는 6개월
        await conn.execute("UPDATE documents SET doc_type = 'ojt_guide' WHERE doc_id = 'T-ALL'")
        try:
            async with aged(conn, verified=days_ago(214), published=days_ago(214), head=HEAD.format("ch01")):
                assert await badges(client, email) == ("unverified",) * 3
        finally:
            await conn.execute("UPDATE documents SET doc_type = 'official_policy' WHERE doc_id = 'T-ALL'")
    api(run)


def test_stale_reference_cases_and_templates_in_search_carry_the_badge(api):
    async def run(client, conn):
        old = days_ago(214)
        async with aged(conn, "T-BENCH", verified=old, published=old):
            r = (await search(client, None, query="공공기관 출장비 정산 기준 참고 자료", include_benchmark=True)).json()
            assert [(d["doc_id"], d["version_badge"]) for d in r["reference_cases"]] == [("T-BENCH", "unverified")]
    api(run)


def test_unverified_lookup_only_for_visible_current_docs(with_pool):
    """`unverified_doc_ids` 는 요청자가 볼 수 있는 현행 문서만 계산 대상: 볼 수 없는 문서·현행이 아닌 문서·종료된 문서·없는 ID 는 낡았어도 빈 집합."""
    from conftest import FakeEmbedder
    from test_current_version import ends
    from test_retrieve import cfg, user

    from rag_api.knowledge_engine.engine import KnowledgeEngine
    hr, today = user("ACTIVE", "HR"), datetime.now(KST).date()
    old = days_ago(214)
    # T-ALL: hr 가 볼 수 있음(양성 대조). T-FIN·T-LGL: 현행이지만 hr 가 못 봄. T-ECO: 다른 팩. T-RETIRED·T-PENDING·T-BADPTR: 현행이 아님
    ids = ["T-ALL", "T-FIN", "T-LGL", "T-ECO", "T-RETIRED", "T-PENDING", "T-BADPTR", "NO-SUCH"]

    async def run(pool):
        e = KnowledgeEngine(pool, FakeEmbedder(), cfg())
        lookup = lambda: e.unverified_doc_ids(ids, hr, today=today)  # noqa: E731
        async with pool.acquire() as conn:
            assert await lookup() == set()  # 낡지 않은 상태
            async with aged(conn, "T-ALL", verified=old, published=old), aged(conn, "T-FIN", verified=old, published=old), \
                    aged(conn, "T-LGL", verified=old, published=old), aged(conn, "T-ECO", verified=old, published=old):
                assert await lookup() == {"T-ALL"}
                async with ends(conn, "T-ALL", today - timedelta(days=1)):  # 종료된 현행 판은 현행이 아님
                    assert await lookup() == set()
                assert await e.unverified_doc_ids(ids, user("ACTIVE", "FIN"), today=today) == {"T-ALL", "T-FIN"}  # 요청자마다 다름
                assert await e.unverified_doc_ids([], hr, today=today) == set()
    with_pool(run)
