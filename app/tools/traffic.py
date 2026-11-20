"""공항 시간대별 이용 흐름 (한국공항공사 공항 14곳).

1순위: 한국공항공사 '전국공항 시간대별 탑승승객 수 조회'(daily-expect-passenger) — 시간대별 승객 수.
       활용신청 전이거나 응답 형식을 읽지 못하면 2순위로 넘어간다.
2순위: 이미 쓰는 실시간 운항 정보(출발·도착)로 시간대별 편수를 센다. 모든 공항에서 동작한다.
어느 자료를 썼는지 source 와 note 에 밝힌다.
"""
import re
import time

from app import airports
from app.tools import flights
from app.tools.common import ApiError, fetch
from app.tools.registry import Tool, register

EXPECT_URL = "https://apis.data.go.kr/B551178/daily-expect-passenger"
TTL = 6 * 3600
DATE_KEYS = ("date", "dt", "ymd", "day")
SKIP_KEYS = ("airport", "apcd", "iata", "code", "nm", "name", "tof", "aod", "line", "io")


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _hour_from(value):
    m = re.match(r"^\s*(\d{1,2})(?:\D|$)", str(value or ""))
    if not m:
        return None
    h = int(m.group(1))
    return h if 0 <= h <= 23 else None


def _hour_in_key(key):
    """'T10', 'H10', 'HR_10', '10_11', 'TIME10' 처럼 키 안에 시간이 든 넓은 형식."""
    m = re.search(r"(?:^|[^0-9])([01]?\d|2[0-3])(?:[^0-9]|$)", key)
    if m and re.search(r"(?i)^(t|h|hr|hour|time|tm|psg|pax|cnt)?_?\d", key):
        return int(m.group(1))
    return None


def _direction(item):
    for k, v in item.items():
        if re.search(r"(?i)aod|io$|dep_arr|inout", k):
            val = str(v or "").upper()
            if val.startswith(("D", "O", "출")):
                return "departure"
            if val.startswith(("A", "I", "도", "입")):
                return "arrival"
    return "total"


def parse_expected(items):
    """응답 형식을 모르므로 두 가지를 모두 읽는다. 읽은 시간대가 없으면 None."""
    hours = {}
    hour_key = None
    for item in items:
        keys = {k.lower(): k for k in item}
        if hour_key is None:
            for low, orig in keys.items():
                if re.search(r"hr|hour|time|tm|시간", low) and not any(d in low for d in DATE_KEYS) \
                        and _hour_from(item[orig]) is not None:
                    hour_key = orig
                    break
        direction = _direction(item)
        if hour_key and hour_key in item:                 # 긴 형식: 한 줄 = 한 시간대
            h = _hour_from(item[hour_key])
            if h is None:
                continue
            total = 0.0
            for k, v in item.items():
                low = k.lower()
                if k == hour_key or any(s in low for s in SKIP_KEYS) or any(d in low for d in DATE_KEYS):
                    continue
                n = _num(v)
                if n is not None:
                    total += n
            slot = hours.setdefault(h, {"departure": 0.0, "arrival": 0.0, "total": 0.0})
            slot[direction] += total
        else:                                             # 넓은 형식: 키마다 시간대
            for k, v in item.items():
                h = _hour_in_key(k)
                n = _num(v)
                if h is None or n is None:
                    continue
                slot = hours.setdefault(h, {"departure": 0.0, "arrival": 0.0, "total": 0.0})
                slot[direction] += n
    if not hours:
        return None
    out = []
    for h in sorted(hours):
        s = hours[h]
        out.append({"hour": h, "departure": round(s["departure"]), "arrival": round(s["arrival"]),
                    "value": round(s["departure"] + s["arrival"] + s["total"])})
    return out


# 포털 목록에는 주소 끝(오퍼레이션)이 적혀 있지 않다. 다른 한국공항공사 서비스처럼 /info 일 수 있어 차례로 시험한다.
EXPECT_PATHS = ("", "/info", "/list")
FAIL_HOLD = 30 * 60
_expect_state = {"path": None, "fail_until": 0.0, "error": None}


def _expected(code, ymd):
    now = time.time()
    if _expect_state["fail_until"] > now:
        raise ApiError("PORTAL", _expect_state["error"])
    params = {"schStDate": ymd, "schEdDate": ymd, "schAirport": code, "numOfRows": 100, "pageNo": 1}
    paths = [_expect_state["path"]] if _expect_state["path"] is not None else list(EXPECT_PATHS)
    last = None
    for path in paths:
        try:
            res = fetch(EXPECT_URL + path, params, ttl=TTL)
        except ApiError as e:
            last = e
            continue
        _expect_state["path"] = path
        rows = parse_expected(res["items"])
        fields = sorted({k for i in res["items"][:3] for k in i})
        return rows, fields
    _expect_state.update(path=None, fail_until=now + FAIL_HOLD,
                         error=f"{last.message if last else '호출 실패'} (30분 뒤 다시 시도)")
    raise last or ApiError("PORTAL", "호출 실패")


def _from_flights(code, ymd):
    dep = flights.search_departures(code, date=ymd, from_time="0000", to_time="2359")
    arr = flights.search_arrivals(code, date=ymd, from_time="0000", to_time="2359")
    hours = {h: {"hour": h, "departure": 0, "arrival": 0, "value": 0} for h in range(24)}
    for rows, key in ((dep["flights"], "departure"), (arr["flights"], "arrival")):
        for r in rows:
            when = r.get("estimated") or r.get("scheduled") or ""
            h = _hour_from(when[11:13]) if len(when) >= 13 else None
            if h is None:
                continue
            hours[h][key] += 1
            hours[h]["value"] += 1
    return [hours[h] for h in range(24)], dep["count"] + arr["count"]


def get_hourly_traffic(airport, date=None):
    code = (airport or "").strip().upper()
    if code not in airports.KAC_AIRPORTS:
        raise ValueError("시간대별 흐름은 한국공항공사 공항(김포·김해·제주 등 14곳)만 제공합니다. 인천은 승객 예고를 쓰십시오.")
    ymd = flights._date(date)
    expect_error = None
    try:
        rows, fields = _expected(code, ymd)
        if rows:
            return {"airport": code, "airport_name": airports.NAMES[code], "date": ymd, "source": "expected_passengers",
                    "unit": "명", "rows": rows, "fields": fields, "count": len(rows),
                    "note": "한국공항공사 전국공항 시간대별 탑승승객 수 자료입니다."}
        expect_error = f"시간대별 승객 수 응답에서 시간대를 읽지 못했습니다 (항목: {', '.join(fields) or '없음'})."
    except ApiError as e:
        expect_error = e.message
    rows, total = _from_flights(code, ymd)
    return {"airport": code, "airport_name": airports.NAMES[code], "date": ymd, "source": "flights",
            "unit": "편", "rows": rows, "count": total,
            "note": "시간대별 출발·도착 편수로 이용 흐름을 보여 줍니다 (실시간 운항 정보 기준).",
            "fallback_reason": expect_error}


register(Tool(
    name="get_hourly_traffic",
    description="한국공항공사 공항(김포·김해·제주·대구·청주 등 14곳)의 시간대별 이용 흐름(예상 승객 수, 없으면 출발·도착 편수)을 "
                "볼 때 쓴다. '몇 시가 붐벼?', '오후에 사람 많아?' 같은 질문.",
    parameters={"type": "object", "properties": {
        "airport": {"type": "string", "description": "IATA 코드"}, "date": {"type": "string", "description": "YYYYMMDD"}},
        "required": ["airport"]},
    func=get_hourly_traffic))
