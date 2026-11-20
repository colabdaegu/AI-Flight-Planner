"""체크인(탑승수속) 마감 기준. 공항이 아니라 항공사가 정하지만, 국내 공항에서는 주요 항공사 기준이 같다.

- 국내선: 출발 30분 전 마감 (대한항공 국내선 안내 '체크인은 출발 30분 전에 마감', 아시아나 '국내선 출발 30분 전 전 공항')
- 국제선: 출발 60분 전 마감 (대한항공 국제선 안내 '체크인은 출발 1시간 전에 마감')
공항별로 다르게 바꿀 수 있게 표로 둔다. 항공사·노선(미주 등)마다 더 이를 수 있어 화면에 '항공사 확인' 안내를 붙인다.
"""
from datetime import datetime, timedelta

from app import airports

DEFAULT = {"domestic": 30, "international": 60}
# 공항별 (국내선, 국제선) 분. 무안은 국제선만 운영하던 공항이지만 같은 기준을 둔다.
RULES = {code: dict(DEFAULT) for code in airports.NAMES}
SOURCE = "대한항공·아시아나항공 탑승수속 안내 (국내선 출발 30분 전, 국제선 출발 1시간 전)"
FMT = "%Y-%m-%d %H:%M"


def line_of(airport, destination=None):
    """국내선이면 'domestic', 국제선이면 'international'. 목적지를 모르면 인천=국제선, 그 외=국내선으로 본다."""
    if destination:
        return "domestic" if airports.is_domestic(destination) and airports.is_domestic(airport) else "international"
    return "international" if airport == airports.ICN else "domestic"


def minutes(airport, line):
    return RULES.get(airport, DEFAULT).get(line, DEFAULT[line])


def deadline(departure, airport, line):
    """출발 시각('YYYY-MM-DD HH:MM')에서 체크인 마감 시각을 계산한다."""
    dep = datetime.strptime(departure, FMT)
    return (dep - timedelta(minutes=minutes(airport, line))).strftime(FMT)
