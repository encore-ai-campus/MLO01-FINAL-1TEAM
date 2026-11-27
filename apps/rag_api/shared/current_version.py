"""현행 판 조건(경계 계약 4.1). 검색·직접 열기 거부 판정·통계·대표 조각·문서 목록·원문 보기가 모두 이 조건으로 현행 판을 고릅니다.

"오늘"은 앱이 요청마다 한국 날짜로 한 번 계산해 넘깁니다(같은 요청의 배지도 같은 값). DB 의 CURRENT_DATE 는 쓰지 않습니다(DB 시간대에 따라 하루 어긋남).
"""
from datetime import date, datetime, timedelta, timezone

from rag_api.shared.sql import Sql

# 한국은 1988년 이후 서머타임 없음. "오늘"은 서버 시간대와 관계없이 한국 날짜(검색 API 계획 6절)
KST = timezone(timedelta(hours=9))


def today_kst() -> date:
    return datetime.now(KST).date()


def current_predicate(today: date, d: str = "d", v: str = "v") -> Sql:
    """문서 `d`·그 현행 버전 `v`: 포인터가 가리키는 active 판이고, 종료일이 없거나 오늘 이후(종료일 당일까지 현행)."""
    return Sql(f"{d}.current_version_id = {v}.version_id AND {v}.version_status = 'active'"
               f" AND ({v}.effective_to IS NULL OR {v}.effective_to >= $?)", (today,))
