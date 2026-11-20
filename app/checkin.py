"""체크인 마감 계산.

- 체크인 마감 = 출발 시각 − 공항 체크인 시간(분)
- 공항 체크인 시간을 사용자가 입력하면 그 값을 쓴다.
- 입력이 없을 때만 노선별 기본값을 쓴다: 국내선 60분, 국제선 120분.
  공항·항공사마다 다르므로 공항별 표는 두지 않는다.
"""
from datetime import datetime, timedelta

from app import airports

DEFAULT = {"domestic": 60, "international": 120}
FMT = "%Y-%m-%d %H:%M"


def line_of(airport, destination=None):
    """국내선이면 'domestic', 국제선이면 'international'. 목적지를 모르면 인천=국제선, 그 외=국내선으로 본다."""
    if destination:
        return "domestic" if airports.is_domestic(destination) and airports.is_domestic(airport) else "international"
    return "international" if airport == airports.ICN else "domestic"


def minutes(line, given=None):
    """공항 체크인 시간(분). 입력값(given)이 있으면 그 값, 없으면 노선별 기본값."""
    if given is None or given == "":
        return DEFAULT[line]
    return int(given)


def deadline(departure, minutes_before):
    """출발 시각('YYYY-MM-DD HH:MM')에서 체크인 마감 시각을 계산한다."""
    dep = datetime.strptime(departure, FMT)
    return (dep - timedelta(minutes=int(minutes_before))).strftime(FMT)
