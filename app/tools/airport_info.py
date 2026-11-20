"""공항 개요 (로컬 서버 도구). 공항 하나의 혼잡·시설 자료를 외부 도구로 모아 한 번에 돌려준다.

- 인천: 승객 예고(시간대별 예상 인원) + 시설 안내
- 한국공항공사 공항: 실시간 구간별 혼잡(김포·제주·김해·청주·대구만) + 시간대별 흐름 + 매장(전국공항 임대계약 정보)
부분 실패는 그 부분만 ok=false 로 담고 나머지는 그대로 돌려준다 (NF3003).
"""
from app import airports
from app.tools import registry
from app.tools.registry import Tool, register


def get_airport_overview(airport):
    code = (airport or "").strip().upper()
    if code not in airports.NAMES:
        raise ValueError(f"지원하지 않는 공항 코드: {code or '(없음)'}")
    parts = {}
    if code == airports.ICN:
        parts["passenger"] = registry.call("get_incheon_passenger_forecast", selectdate="0")
        parts["facilities"] = registry.call("get_incheon_facilities")
    else:
        if code in airports.CONGESTION_AIRPORTS:
            parts["congestion"] = registry.call("get_airport_congestion", airport=code)
        parts["hourly"] = registry.call("get_hourly_traffic", airport=code)
        parts["commercial"] = registry.call("get_commercial_facilities", airport=code)
    return {"airport": code, "airport_name": airports.NAMES[code],
            "has_congestion": code in airports.CONGESTION_AIRPORTS,
            "has_passenger": code == airports.ICN, "parts": parts}


register(Tool(
    name="get_airport_overview",
    kind="local",
    description="공항 시설 화면용. 공항 하나의 혼잡·시설 자료를 한 번에 모을 때 쓴다 (여러 외부 도구를 묶은 로컬 도구).",
    parameters={"type": "object", "properties": {"airport": {"type": "string", "description": "IATA 코드"}},
                "required": ["airport"]},
    func=get_airport_overview))
