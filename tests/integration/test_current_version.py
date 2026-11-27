"""현행 판 조건(경계 계약 4.1): 종료일(`effective_to`)이 오늘보다 앞선 현행 판은 매일 종료 처리 전에도 바로 빠짐. 종료일 당일까지는 현행.
시험 문서 T-ALL(ALL_EMP, 협업 그래프의 한 단계)·T-FIN(FIN 전용, 존재 공개)의 현행 판 종료일만 시험 동안 바꾸고 되돌림."""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from conftest import FakeEmbedder
from requester import headers
from test_api_qa import ask
from test_api_search import REQUESTERS, _requester, search  # noqa: F401  (autouse 고정물)
from test_retrieve import CURRENT_CHUNKS, cfg, user

from rag_api.knowledge_engine import channels, term_stats
from rag_api.knowledge_engine.acl import acl_predicate, active_packs
from rag_api.knowledge_engine.engine import KnowledgeEngine
from rag_api.shared.current_version import KST

HR = user("ACTIVE", "HR")


@asynccontextmanager
async def ends(conn, doc_id: str, end: date):
    """그 문서 현행 판의 종료일을 `end` 로(시험 뒤 비움)."""
    sql = "UPDATE document_versions v SET effective_to = $2 FROM documents d WHERE d.current_version_id = v.version_id AND d.doc_id = $1"
    await conn.execute(sql, doc_id, end)
    try:
        yield
    finally:
        await conn.execute(sql, doc_id, None)


async def open_status(client, email, doc_id):
    r = await client.get(f"/api/v1/documents/{doc_id}", headers=headers(email))
    return r.status_code, (r.json().get("error") or {}).get("code")


def test_ended_current_version_drops_out_of_search_answer_and_open_but_the_end_day_stays(api):
    async def run(client, conn):
        today = datetime.now(KST).date()
        email = REQUESTERS["hr"][0]
        query = "회사 규정 출장비 정산 기준"

        async def seen():
            found = (await search(client, email, query=query)).json()
            docs = {d["doc_id"] for d in found["documents"] + found["reference_templates"]}
            answered = (await ask(client, email, doc_ids=["T-ALL"])).json()  # 고른 문서가 현행이 아니면 근거 부족
            return docs, answered["result"], await open_status(client, email, "T-ALL"), await open_status(client, email, "T-FIN")

        docs, result, all_open, fin_open = await seen()
        assert {"T-ALL", "T-FIN"} - docs == {"T-FIN"} and result == "answered"  # 처음: hr 는 T-ALL 을 보고 T-FIN 은 못 봄
        assert all_open == (200, None) and fin_open == (403, "ACL_DENIED")  # T-FIN 은 존재 공개라 403

        async with ends(conn, "T-ALL", today), ends(conn, "T-FIN", today):  # 종료일 = 오늘이면 그대로 나옴
            assert await seen() == (docs, "answered", (200, None), (403, "ACL_DENIED"))
        async with ends(conn, "T-ALL", today - timedelta(days=1)), ends(conn, "T-FIN", today - timedelta(days=1)):  # 어제면 바로 빠짐
            gone, result, all_open, fin_open = await seen()
            assert gone == docs - {"T-ALL"} and result == "insufficient_evidence"
            assert all_open == (404, "NOT_FOUND") and fin_open == (404, "NOT_FOUND")  # 직접 열기 거부 판정도 "없는 문서"로
        assert await seen() == (docs, "answered", (200, None), (403, "ACL_DENIED"))  # 되돌리면 다시 나옴
    api(run)


def test_ended_current_version_drops_out_of_every_engine_path(with_pool):
    """같은 조건을 쓰는 곳: 대표 조각·그래프 출처·그래프 대상 is_active·존재 공개 안내·거부 판정(hidden)·고른 문서·통계 N.
    오늘을 직접 넘겨(종료일 2026-12-01) 날짜가 지나는 경계를 본다: 12-01 까지 현행, 12-02 부터 아님."""
    end, on, after = date(2026, 12, 1), date(2026, 12, 1), date(2026, 12, 2)
    engine = lambda pool: KnowledgeEngine(pool, FakeEmbedder(), cfg())

    async def snapshot(pool, today):
        e = engine(pool)
        where = acl_predicate(HR, active_packs(HR, False), today)
        rep = await channels.representative(pool, where, ["T-ALL", "T-FIN"], "출장비")
        graph = await e.procedure_graph(["T-PUB", "T-ALL"], HR, today=today)  # T-PUB 의 다음 단계 T-ALL·T-ALL 자신이 출처
        async with pool.acquire() as conn:
            await term_stats.refresh(conn, today)
        return {"rep": sorted(r["doc_id"] for r in rep),
                "steps": [(i.source_doc_id, s["doc_id"], s["is_active"]) for i in graph for s in i.meta["next_steps"] if s["doc_id"] in ("T-ALL", "T-FIN")],
                "hints": [h.doc_id for h in await e.discoverable_hints("재무 규정", HR, today=today)],
                "hidden": await e.hidden_doc_ids(["T-FIN", "T-ALL"], HR, today=today),
                "visible": await e.visible_doc_ids(["T-ALL", "T-HR"], HR, today=today),
                "n": term_stats.current().n}

    async def run(pool):
        base = await snapshot(pool, on)
        assert base["rep"] == ["T-ALL"] and base["hidden"] == ["T-FIN"] and base["hints"] == ["T-FIN"] and base["visible"] == ["T-ALL", "T-HR"]
        assert ("T-PUB", "T-ALL", True) in base["steps"] and ("T-ALL", "T-FIN", True) not in base["steps"]  # T-FIN 은 hr 가 볼 수 없어 그래프에서 빠짐
        async with pool.acquire() as conn, ends(conn, "T-ALL", end), ends(conn, "T-FIN", end):
            assert await snapshot(pool, on) == base  # 종료일 당일은 그대로
            gone = await snapshot(pool, after)
        assert gone["rep"] == [] and gone["hidden"] == [] and gone["hints"] == [] and gone["visible"] == ["T-HR"]
        assert ("T-PUB", "T-ALL", False) in gone["steps"] and not [s for s in gone["steps"] if s[0] == "T-ALL"]  # 종료된 대상은 폐기로 보임, 출처는 빠짐
        assert gone["n"] == base["n"] - len(CURRENT_CHUNKS["T-ALL"]) - len(CURRENT_CHUNKS["T-FIN"])  # 통계 N 에서도 빠짐
        assert await snapshot(pool, on) == base  # 되돌리면 그대로
    try:
        with_pool(run)
    finally:
        term_stats._current = None


def test_korean_today_end_day_stays_and_yesterdays_end_drops(with_pool):
    """요청이 한국 날짜를 `today` 로 넘길 때: 어제 종료된 판은 빠지고 오늘 종료되는 판은 남음."""
    async def run(pool):
        e, kst = KnowledgeEngine(pool, FakeEmbedder(), cfg()), datetime.now(KST).date()
        async with pool.acquire() as conn:
            async with ends(conn, "T-ALL", kst):
                assert await e.visible_doc_ids(["T-ALL"], HR, today=kst) == ["T-ALL"]
            async with ends(conn, "T-ALL", kst - timedelta(days=1)):
                assert await e.visible_doc_ids(["T-ALL"], HR, today=kst) == []
    with_pool(run)
