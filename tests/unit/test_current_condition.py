"""현행 판 조건(경계 계약 4.1)의 SQL 조각과 "오늘"."""
from datetime import date, datetime, timezone

from rag_api.knowledge_engine import channels, term_stats
from rag_api.shared import current_version
from rag_api.shared.current_version import current_predicate, today_kst
from rag_api.shared.sql import render

TODAY = date(2026, 11, 25)


def test_current_predicate_is_one_fragment_with_today_as_a_value_slot():
    p = current_predicate(TODAY)
    assert p.text == ("d.current_version_id = v.version_id AND v.version_status = 'active'"
                      " AND (v.effective_to IS NULL OR v.effective_to >= $?)")
    assert p.args == (TODAY,)  # 글자로 이어 붙이지 않고 값 자리로
    x = current_predicate(TODAY, "x", "y")
    assert x.text.startswith("x.current_version_id = y.version_id AND y.version_status = 'active' AND (y.effective_to IS NULL")


def test_today_kst_follows_the_korean_calendar_not_utc(monkeypatch):
    """UTC 11-24 16:00 = 한국 11-25 01:00: 서버 시간대와 관계없이 한국 날짜."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            utc = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
            return utc.replace(tzinfo=None) if tz is None else utc.astimezone(tz)  # tz 없이 부르면 UTC 서버의 지역 시각
    monkeypatch.setattr(current_version, "datetime", Frozen)
    assert today_kst() == date(2026, 11, 25)


def test_term_stats_sql_takes_today_as_a_date_literal_in_both_queries():
    sql = term_stats.TERM_STATS_SQL
    assert sql.count("{today}") == 2 and sql.count("v.effective_to >= '{today}'::date") == 2  # 청크 수 N 과 ts_stat 안쪽 질의 둘 다
    assert "'2026-11-25'::date" in sql.replace("{today}", TODAY.isoformat())


def test_graph_targets_refills_is_active_with_the_current_condition_and_today():
    sql, args = render(channels.GRAPH_TARGETS_SQL, current=current_predicate(TODAY), where=channels.Sql("d.x = $?", ("W",)),
                       target_ids=["A"])
    assert "coalesce(d.current_version_id = v.version_id AND v.version_status = 'active' AND (v.effective_to IS NULL OR v.effective_to >= $1), false) AS is_active" in sql
    assert args[0] == TODAY
