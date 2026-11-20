"""공항 혼잡도 (한국공항공사 '공항 혼잡도 정보', v1: 김포·제주 / v2: 김해·청주·대구).

이 API 는 **지금 한 시점**의 국내선 구간별 혼잡 단계만 준다 (공항마다 1건). 시간대별 기록이 아니다.
구간: 1구간 체크인→신분확인, 2구간 신분확인→보안검색, 3구간 보안검색→탑승 (제공기관 설명).
CGDR_A/B/C 를 1·2·3구간으로 읽는다. 시간대별 흐름은 get_hourly_traffic 이 맡는다.
"""
import re

from app import airports
from app.tools.common import fetch, pick
from app.tools.registry import Tool, register

BASE = "https://apis.data.go.kr/B551178/airport-congestion"
SEGMENTS = (("level_a", "1구간 체크인→신분확인"), ("level_b", "2구간 신분확인→보안검색"),
            ("level_c", "3구간 보안검색→탑승"))
SKIP = {"iata_apcd", "prc_hr"}


def _time_label(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 3:
        return f"{digits[:-2].zfill(2)}:{digits[-2:]}"
    if digits:
        return f"{digits.zfill(2)}:00"
    return None


def get_airport_congestion(airport):
    code = (airport or "").strip().upper()
    version = airports.CONGESTION_AIRPORTS.get(code)
    if version is None:
        raise ValueError(f"실시간 혼잡도 자료가 없는 공항: {code or '(없음)'}")
    parsed = fetch(f"{BASE}/{version}", {"pageNo": 1, "numOfRows": 100})
    rows = []
    for i in parsed["items"]:
        if (pick(i, "IATA_APCD") or "").upper() != code:
            continue
        row = {"time": _time_label(pick(i, "PRC_HR")), "hour": pick(i, "PRC_HR"),
               "level": pick(i, "CGDR_ALL_LVL"),
               "level_a": pick(i, "CGDR_A_LVL"), "level_b": pick(i, "CGDR_B_LVL"), "level_c": pick(i, "CGDR_C_LVL"),
               # 이름을 모르는 나머지 항목(소요시간 등)도 그대로 넘긴다. 화면이 숫자 항목을 함께 보여 준다.
               "extra": {k: v for k, v in i.items() if k.lower() not in SKIP and not k.upper().startswith("CGDR_")}}
        rows.append(row)
    return {"airport": code, "airport_name": airports.NAMES[code], "count": len(rows), "rows": rows,
            "segments": [{"key": k, "label": label} for k, label in SEGMENTS],
            "note": "실시간 한 시점의 국내선 구간별 혼잡 단계입니다 (제공기관 단계 그대로). 시간대별 기록은 제공하지 않습니다."}


register(Tool(
    name="get_airport_congestion",
    description="김포·제주·김해·청주·대구 공항 국내선의 '지금' 구간별 혼잡 단계(체크인→신분확인→보안검색→탑승)를 볼 때 쓴다. "
                "시간대별 흐름이 필요하면 get_hourly_traffic 을 쓴다.",
    parameters={"type": "object", "properties": {"airport": {"type": "string", "description": "IATA 코드"}},
                "required": ["airport"]},
    func=get_airport_congestion))
