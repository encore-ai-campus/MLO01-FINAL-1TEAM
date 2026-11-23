"""검색 통계(계획 3.7): 현행 청크 전체의 토큰별 청크 빈도(df)와 청크 수(N). 근거 부족 판정(qa)의 IDF 덮음에 씁니다.

`refresh` 를 앱 시작 때와 TERM_STATS_REFRESH_S 마다 부릅니다(main 의 갱신 작업).
"""
import math
from dataclasses import dataclass

# 팩·열람과 관계없이 현행 청크 전체(BM25 통계와 같음). 응답에 내지 않고 판정에만 씀.
# 한 문장이라 N 과 df 가 같은 스냅샷. 단어가 하나도 없어도 N 을 받도록 청크 수 쪽에 LEFT JOIN
TERM_STATS_SQL = """
SELECT n.total, s.word, s.ndoc
FROM (SELECT count(*) AS total
      FROM chunks c JOIN documents d ON d.current_version_id = c.version_id
                    JOIN document_versions v ON v.version_id = c.version_id
      WHERE v.version_status = 'active') n
LEFT JOIN ts_stat($$
      SELECT c.tsv FROM chunks c JOIN documents d ON d.current_version_id = c.version_id
                                 JOIN document_versions v ON v.version_id = c.version_id
      WHERE v.version_status = 'active'$$) s ON TRUE
"""


@dataclass(frozen=True)
class TermStats:
    n: int
    df: dict[str, int]

    def idf(self, token: str) -> float:
        """ln(1 + (N − df + 0.5) / (df + 0.5)). 말뭉치에 없는 토큰(df 0)이 가장 큼."""
        df = self.df.get(token, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def coverage(self, query_tokens: list[str], lexemes: list[str]) -> float | None:
        """질의 토큰(중복 없이) IDF 합 중 청크 lexemes 가 가진 몫. 질의 토큰이 없으면 None."""
        q = set(query_tokens)
        if not q:
            return None
        have = set(lexemes)
        return sum(self.idf(t) for t in q if t in have) / sum(self.idf(t) for t in q)


def top_coverage(result, tokens: list[str], stats: TermStats, top_n: int) -> float | None:
    """후보 상위 `top_n` 청크의 IDF 덮음 중 최댓값. 질의 토큰이 없으면 None."""
    if not tokens:
        return None
    return max((stats.coverage(tokens, c.lexemes) or 0.0 for c in result.chunks[:top_n]), default=None)


def insufficient(result, tokens: list[str], stats: TermStats, cfg) -> bool:
    """근거 부족(R3, A46, qa 계획 3.3). qa 와 평가 스크립트가 같이 씀.
    청크 0건, 또는 덮음 < INSUFFICIENT_COVERAGE_MIN, 또는 벡터 1위 코사인 < INSUFFICIENT_COSINE_MIN.
    임베딩 장애면 코사인이 없어 덮음만, 질의 토큰이 없으면 덮음이 없어 코사인만 봄.
    `tokens` 는 원래 질문의 tokenize_ko(확장 토큰 아님, 기준값을 잰 방식과 같음)."""
    if not result.chunks:
        return True
    coverage = top_coverage(result, tokens, stats, cfg.insufficient_coverage_chunks)
    cosine = result.vector_top1_cosine
    return (coverage is not None and coverage < cfg.insufficient_coverage_min) or \
        (cosine is not None and cosine < cfg.insufficient_cosine_min)


_current: TermStats | None = None


def current() -> TermStats:
    if _current is None:
        raise RuntimeError("검색 통계를 아직 올리지 않았습니다")
    return _current


async def refresh(conn) -> None:
    """현행 청크 전체의 통계를 다시 읽음."""
    global _current
    rows = await conn.fetch(TERM_STATS_SQL)
    _current = TermStats(rows[0]["total"], {r["word"]: r["ndoc"] for r in rows if r["word"] is not None})
