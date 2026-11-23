import asyncio
import math

from rag_api.knowledge_engine import term_stats
from rag_api.knowledge_engine.term_stats import TermStats


def test_idf_and_coverage_by_hand():
    st = TermStats(n=10, df={"a": 1, "b": 5})
    # ln(1 + 9.5/1.5), ln(1 + 5.5/5.5), ln(1 + 10.5/0.5) 를 손으로 계산한 값
    assert math.isclose(st.idf("a"), 1.992430, rel_tol=1e-6)
    assert math.isclose(st.idf("b"), 0.693147, rel_tol=1e-6)
    assert math.isclose(st.idf("c"), 3.091042, rel_tol=1e-6)  # 말뭉치에 없는 토큰
    assert math.isclose(st.coverage(["a", "b", "c", "a"], ["a", "x"]), 0.344913, rel_tol=1e-5)  # 1.99243 / 5.776619
    assert math.isclose(st.coverage(["a", "b", "c"], ["b", "a"]), 0.464905, rel_tol=1e-5)
    assert st.coverage(["a"], ["a"]) == 1.0 and st.coverage(["a"], []) == 0.0
    assert st.coverage([], ["a"]) is None


class FakeConn:
    def __init__(self, rows):
        self.rows, self.fetches = rows, 0

    async def fetch(self, sql):
        assert sql == term_stats.TERM_STATS_SQL
        self.fetches += 1
        return self.rows


def test_refresh_loads_rows():
    term_stats._current = None
    conn = FakeConn([{"total": 3, "word": "a", "ndoc": 2}, {"total": 3, "word": "b", "ndoc": 1}])
    asyncio.run(term_stats.refresh(conn))
    assert (term_stats.current().n, term_stats.current().df, conn.fetches) == (3, {"a": 2, "b": 1}, 1)
    conn.rows = [{"total": 0, "word": None, "ndoc": None}]  # 청크가 없음
    asyncio.run(term_stats.refresh(conn))
    assert (term_stats.current().n, term_stats.current().df, conn.fetches) == (0, {}, 2)  # 부를 때마다 다시 읽음
