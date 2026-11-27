"""낡은 문서 배지 `unverified`(경계 계약 4.11): 달력 개월 더하기, 기준 날짜, 긴 주기 대상, scheduled 우선."""
from datetime import date, datetime

import pytest

from rag_api.contracts.common import add_months, is_unverified, version_badge
from rag_api.documents.service import is_stale
from rag_api.shared.config import Config

CFG = Config(database_url="x")
TODAY = date(2026, 11, 25)


@pytest.mark.parametrize("start, months, end", [
    (date(2026, 3, 15), 6, date(2026, 9, 15)),
    (date(2026, 8, 31), 6, date(2027, 2, 28)),   # 달 끝은 그달 마지막 날
    (date(2027, 8, 31), 6, date(2028, 2, 29)),   # 윤년
    (date(2026, 3, 15), 12, date(2027, 3, 15)),
    (date(2026, 2, 28), 6, date(2026, 8, 28)),   # 말일이 아니면 같은 날(달 끝으로 늘리지 않음)
    (date(2026, 12, 31), 2, date(2027, 2, 28)),  # 해를 넘김
    (date(2024, 2, 29), 12, date(2025, 2, 28)),  # 윤일 + 12개월
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2026, 7, 15), 6, date(2027, 1, 15)),
])
def test_add_months_is_calendar_months_clamped_to_month_end(start, months, end):
    assert add_months(start, months) == end


@pytest.mark.parametrize("basis, long, stale", [
    (date(2026, 5, 25), False, False),  # 기준 + 6개월 = 오늘 → 아님
    (date(2026, 5, 24), False, True),   # 하루 지나면 낡음
    (date(2025, 11, 25), True, False),  # 긴 주기(12개월)도 같은 경계
    (date(2025, 11, 24), True, True),
    (date(2026, 5, 24), True, False),   # 같은 날짜가 긴 주기면 아직
])
def test_is_unverified_boundary(basis, long, stale):
    assert is_unverified(basis, long, TODAY, CFG) is stale


def test_is_unverified_uses_the_configured_months():
    cfg = Config(database_url="x", verify_interval_months=1, verify_long_interval_months=2)
    assert is_unverified(date(2026, 10, 24), False, TODAY, cfg) and not is_unverified(date(2026, 10, 25), False, TODAY, cfg)
    assert is_unverified(date(2026, 9, 24), True, TODAY, cfg) and not is_unverified(date(2026, 9, 25), True, TODAY, cfg)


def test_version_badge_scheduled_wins_over_unverified():
    future, past = date(2026, 11, 26), date(2026, 11, 25)
    assert version_badge(future, TODAY, stale=True) == "scheduled" and version_badge(future, TODAY) == "scheduled"
    assert version_badge(past, TODAY, stale=True) == "unverified" and version_badge(past, TODAY) is None  # 시행일 당일은 시행 예정 아님


HEAD = "---\ndoc_id: D\nchapter_id: {}\n---\n# 제목\n"


def stale(doc_type="official_policy", verified=None, created=datetime(2026, 5, 24, 9), head=HEAD.format("ch03")):
    return is_stale(doc_type, verified, created, head, TODAY, CFG)


def test_basis_is_the_later_of_verified_and_published():
    assert stale(created=datetime(2026, 5, 24, 9)) and not stale(created=datetime(2026, 5, 25, 23, 59))  # 날짜로 비교(시각 무시)
    assert not stale(verified=datetime(2026, 11, 1), created=datetime(2025, 1, 1))  # 확인이 더 늦음
    assert not stale(verified=datetime(2025, 1, 1), created=datetime(2026, 11, 1))  # 게시(개정)가 더 늦음 = 확인으로 봄
    assert stale(verified=datetime(2025, 1, 1), created=datetime(2025, 2, 1))       # 둘 다 오래됨
    assert stale(verified=None, created=datetime(2025, 1, 1))                       # 확인 시각 없음 → 게시 시각만


def test_long_interval_only_for_configured_chapter_and_official_policy():
    old = datetime(2025, 12, 1)  # 11개월 전: 짧은 주기면 낡음, 긴 주기면 아직
    assert not stale(created=old, head=HEAD.format("ch01"))
    assert stale(created=old, head=HEAD.format("ch02")) and stale(created=old, head=HEAD.format("ch10"))  # 다른 장
    for doc_type in ("ojt_guide", "standard_template", "benchmark_reference"):  # 다른 종류는 ch01 이어도 짧은 주기
        assert stale(doc_type, created=old, head=HEAD.format("ch01")), doc_type
    assert stale(created=datetime(2025, 11, 24), head=HEAD.format("ch01"))  # 12개월도 지나면 낡음


@pytest.mark.parametrize("head", ["", "본문만\n", "---\ndoc_id: D\n---\n", "---\n: [\n---\n", "---\nchapter_id: [ch01]\n---\n",
                                  "---\nchapter_id: 1\n---\n", "---\n- a\n---\n"])
def test_missing_or_unreadable_chapter_is_the_short_interval(head):
    assert stale(created=datetime(2025, 12, 1), head=head)  # 11개월: 긴 주기였다면 낡지 않음


def test_other_long_interval_chapters_from_config():
    cfg = Config(database_url="x", verify_long_interval_chapters="ch01,ch02")
    assert not is_stale("official_policy", None, datetime(2025, 12, 1), HEAD.format("ch02"), TODAY, cfg)
    assert is_stale("official_policy", None, datetime(2025, 12, 1), HEAD.format("ch03"), TODAY, cfg)
