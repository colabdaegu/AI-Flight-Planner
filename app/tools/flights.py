"""운항 조회 도구: 출발 시간표, 도착 시간표, 편명 조회.

인천(ICN)은 인천국제공항공사 여객기 운항 현황, 그 외 공항은 한국공항공사 실시간 운항 자료를 쓴다.
한 번에 받는 건수가 제한돼 있어서 페이지를 나눠 받는다 (total 과 비교해 끝까지 받는다).
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app import airports
from app.tools.common import ApiError, fetch, flight_no_key, maps_link, pick, to_dt
from app.tools.registry import Tool, register

IIAC_DEP = "http://apis.data.go.kr/B551177/statusOfAllFltDeOdp/getFltDeparturesDeOdp"
IIAC_PAX_DEP = "https://apis.data.go.kr/B551177/StatusOfPassengerFlightsDeOdp/getPassengerDeparturesDeOdp"
IIAC_PAX_ARR = "https://apis.data.go.kr/B551177/StatusOfPassengerFlightsDeOdp/getPassengerArrivalsDeOdp"
KAC_DEP = "https://apis.data.go.kr/B551178/flight-status/depart"
KAC_ARR = "https://apis.data.go.kr/B551178/flight-status/arrival"
KAC_INFO = "https://apis.data.go.kr/B551178/flight-status/info"     # 오늘 실시간, 탑승구 포함
KST = timezone(timedelta(hours=9))

IIAC_PAGE = 1000      # 인천 한 페이지 건수
IIAC_MAX_PAGES = 4
KAC_PAGE = 100        # 한국공항공사 한 페이지 건수 (500 으로 요청하면 제공기관이 04 HTTP 오류를 낸다)
KAC_MAX_PAGES = 6


def today_kst():
    return datetime.now(KST).strftime("%Y%m%d")


def _now_hhmm():
    return datetime.now(KST).strftime("%H%M")


def _airport(code):
    code = (code or "").strip().upper()
    if code not in airports.NAMES:
        raise ValueError(f"지원하지 않는 공항 코드: {code or '(없음)'}")
    return code


def _date(ymd):
    if not ymd:
        return today_kst()
    if not re.fullmatch(r"\d{8}", ymd):
        raise ValueError("날짜는 YYYYMMDD 형식이어야 합니다.")
    return ymd


def _hhmm(value, default):
    if not value:
        return default
    if not re.fullmatch(r"\d{4}", value):
        raise ValueError("시간은 HHMM 형식이어야 합니다 (예: 0900).")
    return value


def _window(code, ymd, from_time, to_time):
    """조회 시간 구간. 오늘을 조회하면서 시작 시각을 안 주면 지금 시각부터 받는다."""
    if from_time:
        f_from = _hhmm(from_time, "0000")
    else:
        f_from = _now_hhmm() if ymd == today_kst() else "0000"
    f_to = _hhmm(to_time, "2400" if code == airports.ICN else "2359")
    return f_from, f_to


def _when(value):
    return to_dt(value) or (str(value) if value else None)


def _map(name):
    return maps_link(f"{name} 공항") if name else None


def _flight_no(text):
    return flight_no_key(text)


def _code_or_none(text):
    value = (text or "").strip().upper()
    return value or None


def _other(direction, code, name):
    """출발 조회에서 상대 공항은 목적지, 도착 조회에서는 출발지가 된다."""
    if direction == "departure":
        return {"destination_code": code, "destination_name": name}
    return {"origin_code": code, "origin_name": name}


def _iiac_row(item, direction):
    name = pick(item, "airport")
    row = {
        "provider": "IIAC", "airport": "ICN", "direction": direction,
        "airline": pick(item, "airline"), "flight_no": pick(item, "flightId", "fid"),
        "scheduled": _when(pick(item, "scheduleDateTime")),
        "estimated": _when(pick(item, "estimatedDateTime", "estimatedDatetime")),
        "remark": pick(item, "remark"), "terminal": pick(item, "terminalId", "terminalid"),
        "gate": pick(item, "gateNumber", "gatenumber"), "map_url": _map(name),
        "codeshare": (pick(item, "codeshare", "codeShare") or "").strip().lower() or None,
        "master_flight": _flight_no(pick(item, "masterflightid", "masterFlightId", "masterflight")) or None,
    }
    row.update(_other(direction, pick(item, "airportCode"), name))
    return row


def _kac_row(item, airport, direction):
    if direction == "departure":
        code = pick(item, "arrvAirportCode", "arrAirportCode")
        name = pick(item, "arrAirport")
    else:
        code = pick(item, "depAirportCode")
        name = pick(item, "depAirport")
    row = {
        "provider": "KAC", "airport": airport, "direction": direction,
        "airline": pick(item, "airline"), "flight_no": pick(item, "flightid", "fid"),
        "scheduled": _when(pick(item, "scheduledatetime")),
        "estimated": _when(pick(item, "estimateddatetime")),
        "remark": pick(item, "rmkKor"), "line": pick(item, "line"),
        "terminal": None, "gate": None,            # KAC 목록에는 탑승구 정보가 없다
        "map_url": _map(name),
    }
    row.update(_other(direction, code, name))
    return row


def _fetch_all(url, params, page_size, max_pages):
    """페이지를 나눠 받는다. total 만큼 다 받았거나 더 받을 게 없으면 멈춘다.
    첫 페이지가 제공기관 오류(큰 요청 건수에서 생기는 04 등)로 실패하면 건수를 줄여 한 번 더 시도한다.
    둘째 페이지부터 실패하면 받은 것까지만 쓴다."""
    items = []
    total = None
    for page in range(1, max_pages + 1):
        try:
            res = fetch(url, {**params, "numOfRows": page_size, "pageNo": page})
        except ApiError as e:
            if page == 1 and page_size > 50 and e.code in ("PROVIDER", "HTTP"):
                return _fetch_all(url, params, 50, max_pages * (page_size // 50))
            if page == 1:
                raise
            break
        batch = res["items"]
        items.extend(batch)
        total = res.get("total") or total
        try:
            expected = int(total)
        except (TypeError, ValueError):
            break
        if not batch or len(items) >= expected:
            break
    return items, total


def _board(code, ymd, f_from, f_to, direction, other=None):
    """공항 한쪽(출발 또는 도착) 운항 목록을 받아 정규화한다. 반환: (행 목록, 받은 건수, 전체 건수)."""
    departure = direction == "departure"
    if code == airports.ICN:
        url = IIAC_PAX_DEP if departure else IIAC_PAX_ARR
        params = {"searchday": ymd, "from_time": f_from, "to_time": f_to, "inqtimechcd": "E", "lang": "K"}
        if other:
            params["airport_code"] = other
        items, total = _fetch_all(url, params, IIAC_PAGE, IIAC_MAX_PAGES)
        rows = [_iiac_row(i, direction) for i in items]
    else:
        url = KAC_DEP if departure else KAC_ARR
        params = {"searchday": ymd, "from_time": f_from, "to_time": f_to, "airport_code": code}
        if other:
            params["arr_airport_code" if departure else "dep_airport_code"] = other
        items, total = _fetch_all(url, params, KAC_PAGE, KAC_MAX_PAGES)
        rows = [_kac_row(i, code, direction) for i in items]
    return rows, len(items), total


def merge_codeshares(rows):
    """같은 비행기를 여러 항공사 편명으로 파는 공동운항을 한 줄로 묶는다.
    - 응답에 codeshare(master/slave)·masterflightid 가 있으면 그것을 따른다.
    - 없으면 인천 자료에서 출발 시각·상대 공항·터미널이 모두 같은 편을 같은 비행기로 본다(터미널 정보가 있을 때만).
    묶인 편명은 대표 편의 codeshares 목록에 남긴다."""
    def key(r):
        other = r.get("destination_code") or r.get("origin_code")
        return (r.get("direction"), r.get("scheduled"), other, r.get("terminal"))

    masters, by_no, by_key, out = {}, {}, {}, []
    has_flag = any(r.get("codeshare") for r in rows)
    for r in rows:
        r = dict(r)
        r["codeshares"] = []
        if has_flag and r.get("codeshare") == "slave":
            continue
        out.append(r)
        by_no[_flight_no(r.get("flight_no"))] = r
        if r.get("terminal") or has_flag:
            by_key.setdefault(key(r), r)
    for r in rows:
        if not (has_flag and r.get("codeshare") == "slave"):
            continue
        master = by_no.get(r.get("master_flight") or "") or by_key.get(key(r))
        if master is not None:
            master["codeshares"].append(r.get("flight_no"))
        else:
            out.append({**r, "codeshares": []})
    if not has_flag:
        merged, seen = [], {}
        for r in out:
            k = key(r)
            if r.get("provider") == "IIAC" and r.get("terminal") and k in seen:
                seen[k]["codeshares"].append(r.get("flight_no"))
                continue
            seen.setdefault(k, r)
            merged.append(r)
        out = merged
    for r in out:
        r.pop("master_flight", None)
    return out


def _result(code, ymd, rows, fetched, total):
    try:
        expected = int(total)
    except (TypeError, ValueError):
        expected = None
    notes = []
    if not rows:
        notes.append("호출은 성공했고 조건에 맞는 운항은 0건입니다.")
    if expected and expected > fetched:
        notes.append(f"전체 {expected}건 중 {fetched}건만 받아와서 일부만 보입니다.")
    return {"airport": code, "airport_name": airports.NAMES[code], "date": ymd,
            "provider": airports.provider_of(code), "count": len(rows), "total": expected,
            "flights": rows, "note": " ".join(notes) or None}


def _search_side(airport, date, from_time, to_time, other_filter, direction, other_key):
    code = _airport(airport)
    ymd = _date(date)
    f_from, f_to = _window(code, ymd, from_time, to_time)
    other = _code_or_none(other_filter)
    rows, fetched, total = _board(code, ymd, f_from, f_to, direction, other)
    if other:                                      # 제공 API 가 거르지 못해도 한 번 더 거른다
        rows = [r for r in rows if r.get(other_key) == other]
    rows.sort(key=lambda r: r["scheduled"] or "")
    rows = merge_codeshares(rows)
    return _result(code, ymd, rows, fetched, total)


def search_departures(airport, date=None, from_time=None, to_time=None, destination=None):
    return _search_side(airport, date, from_time, to_time, destination, "departure", "destination_code")


def search_arrivals(airport, date=None, from_time=None, to_time=None, origin=None):
    return _search_side(airport, date, from_time, to_time, origin, "arrival", "origin_code")


def _match_airport(code, no, ymd):
    f_to = "2400" if code == airports.ICN else "2359"
    rows = []
    for direction in ("departure", "arrival"):
        try:
            board, _, _ = _board(code, ymd, "0000", f_to, direction)
        except ApiError:
            continue
        rows += [r for r in board if _flight_no(r["flight_no"]) == no
                 or no in [_flight_no(c) for c in (r.get("codeshares") or [])]]
    return rows


def _shift(ymd, days):
    return (datetime.strptime(ymd, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _match_dir(code, no, ymd, direction):
    return [r for r in _match_airport(code, no, ymd) if r["direction"] == direction]


def _leg(r):
    """출발 또는 도착 한쪽의 시각·터미널·탑승구."""
    return {"airport": r["airport"], "airport_name": airports.NAMES.get(r["airport"], r["airport"]),
            "scheduled": r.get("scheduled"), "estimated": r.get("estimated"),
            "terminal": r.get("terminal"), "gate": r.get("gate"), "remark": r.get("remark"),
            "provider": r.get("provider")}


def _fill_kac_gate(leg, no):
    """한국공항공사 목록에는 탑승구가 없다. 오늘 편이면 실시간 info 에서 탑승구를 채운다."""
    if not leg or leg.get("gate") or leg["airport"] == airports.ICN or not leg.get("scheduled"):
        return
    ymd = leg["scheduled"][:10].replace("-", "")
    if ymd != today_kst():
        return
    try:
        found = _kac_info(leg["airport"], no, ymd, leg["direction"])
    except Exception:   # 탑승구는 덧붙이는 정보라 실패해도 조회는 계속한다
        return
    for r in found:
        if r.get("gate"):
            leg["gate"] = r["gate"]
            break


def search_flight(flight_no, airport=None, date=None):
    """편명으로 찾는다. 공항을 주지 않으면 인천과 한국공항공사 14개 공항의 하루 출발·도착 목록을 함께 뒤진다.
    같은 비행의 출발 줄과 도착 줄은 한 줄로 합치고, 출발·도착 각각의 날짜·시각·탑승구를 legs 에 담는다.
    - 자정을 넘겨 도착하는 국내 공항 편은 도착 공항의 다음 날 목록에서 도착 시각을 찾는다.
    - 전날 국내 공항에서 떠나 오늘 도착하는 편은 출발 공항의 전날 목록에서 출발 시각을 찾는다.
    - 해외 공항 쪽 시각은 국내 공공데이터에 없어 비워 둔다."""
    no = _flight_no(flight_no)
    if not no:
        raise ValueError("편명을 입력하십시오 (예: KE1041).")
    ymd = _date(date)
    codes = [_airport(airport)] if airport else [airports.ICN, *airports.KAC_AIRPORTS]
    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for found in pool.map(lambda c: _match_airport(c, no, ymd), codes):
            rows += found
    deps = [r for r in rows if r["direction"] == "departure"]
    arrs = [r for r in rows if r["direction"] == "arrival"]
    used = set()
    out = []
    for d in deps:
        dest = d.get("destination_code")
        a = next((x for i, x in enumerate(arrs) if i not in used
                  and x.get("origin_code") == d["airport"] and x["airport"] == dest), None)
        if a is not None:
            used.add(arrs.index(a))
        elif dest in airports.NAMES:
            a = next((x for x in _match_dir(dest, no, _shift(ymd, 1), "arrival")
                      if x.get("origin_code") == d["airport"]), None)
        out.append((d, a))
    for i, a in enumerate(arrs):
        if i in used:
            continue
        origin = a.get("origin_code")
        d = None
        if origin in airports.NAMES:
            d = next((x for x in _match_dir(origin, no, _shift(ymd, -1), "departure")
                      if x.get("destination_code") == a["airport"]), None)
        out.append((d, a))

    result = []
    for d, a in out:
        row = dict(d or a)
        dep_leg = dict(_leg(d), direction="departure") if d else None
        arr_leg = dict(_leg(a), direction="arrival") if a else None
        for leg in (dep_leg, arr_leg):
            _fill_kac_gate(leg, no)
        if d and a:
            row["origin_code"] = d["airport"]
            row["origin_name"] = airports.NAMES.get(d["airport"])
        row["legs"] = {"departure": dep_leg, "arrival": arr_leg}
        row["arr_scheduled"] = a.get("scheduled") if (d and a) else None
        row["arr_estimated"] = a.get("estimated") if (d and a) else None
        if dep_leg and dep_leg.get("gate"):
            row["gate"] = dep_leg["gate"]
        result.append(row)
    result.sort(key=lambda r: (r.get("scheduled") or "", r["direction"] != "departure"))
    first = result[0]["airport"] if result else (codes[0] if len(codes) == 1 else airports.ICN)
    return {"airport": first, "airport_name": airports.NAMES[first], "flight_no": no, "date": ymd,
            "provider": None, "count": len(result), "flights": result, "searched": len(codes),
            "note": None if result else f"{_fmt(ymd)} 국내 {len(codes)}개 공항의 출발·도착 목록에 {no} 편이 없습니다."}


def _fmt(ymd):
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _info_time(ymd, value):
    """info 응답의 시각(HHMM 또는 YYYYMMDDHHMM)을 'YYYY-MM-DD HH:MM' 으로."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 4:
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]} {digits[:2]}:{digits[2:]}"
    return _when(value)


def _kac_info(code, no, ymd, direction):
    """한국공항공사 실시간 운항 info (국내선 D, 국제선 I 두 번). 오늘 자료만 있다."""
    rows, errors = [], []
    departure = direction == "departure"
    for line in ("D", "I"):
        params = {"schLineType": line, "schIOType": "O" if departure else "I", "schAirCode": code,
                  "schStTime": "0000", "schEdTime": "2359"}
        try:
            items, _ = _fetch_all(KAC_INFO, params, KAC_PAGE, 4)
        except ApiError as e:
            errors.append(e)
            continue
        for i in items:
            if _flight_no(pick(i, "airFln")) != no:
                continue
            other = pick(i, "arrivedKor", "arrivedEng") if departure else pick(i, "boardingKor", "boardingEng")
            row = {"provider": "KAC", "airport": code, "direction": direction,
                   "line": "국내" if line == "D" else "국제",
                   "airline": pick(i, "airlineKorean", "airline"), "flight_no": no,
                   "scheduled": _info_time(ymd, pick(i, "std")), "estimated": _info_time(ymd, pick(i, "etd")),
                   "gate": pick(i, "gate"), "terminal": None, "remark": pick(i, "rmkKor"),
                   "map_url": _map(other)}
            row.update(_other(direction, None, other))
            rows.append(row)
    if not rows and len(errors) == 2:
        raise errors[-1]
    return rows


def get_flight_detail(airport, flight_no, date=None, direction="departure"):
    """후보의 편명으로 상세(탑승구·터미널·상태·예상 시각)를 확인한다 (명세 F3003).
    인천: 하루 출발(또는 도착) 목록에서 편명. 한국공항공사: 오늘은 실시간 info(탑승구 포함), 다른 날은 운항 목록."""
    code = _airport(airport)
    no = _flight_no(flight_no)
    if not no:
        raise ValueError("편명을 입력하십시오 (예: KE1041).")
    if direction not in ("departure", "arrival"):
        raise ValueError("direction 은 departure 또는 arrival 이어야 합니다.")
    ymd = _date(date)
    if code == airports.ICN:
        rows, _, _ = _board(code, ymd, "0000", "2400", direction)
        rows = [r for r in rows if _flight_no(r["flight_no"]) == no]
        source = "인천국제공항공사 여객기 운항 현황"
    elif ymd == today_kst():
        rows = _kac_info(code, no, ymd, direction)
        source = "한국공항공사 실시간 운항 정보(info)"
    else:
        rows, _, _ = _board(code, ymd, "0000", "2359", direction)
        rows = [r for r in rows if _flight_no(r["flight_no"]) == no]
        source = "한국공항공사 운항 목록 (탑승구는 당일 실시간 자료에만 있음)"
    return {"airport": code, "airport_name": airports.NAMES[code], "flight_no": no, "date": ymd,
            "direction": direction, "count": len(rows), "rows": rows, "source": source,
            "note": None if rows else "해당 편명·날짜의 운항 정보가 없습니다."}


register(Tool(
    name="search_departures",
    description="공항의 출발 운항 시간표 조회. 인천(ICN)은 인천공항공사, 그 외는 한국공항공사 자료. "
                "날짜(YYYYMMDD)를 생략하면 오늘. 목적지 IATA 코드로 거를 수 있다.",
    parameters={"type": "object", "properties": {
        "airport": {"type": "string", "description": "출발 공항 IATA 코드 (ICN, GMP, CJU, PUS 등)"},
        "date": {"type": "string", "description": "YYYYMMDD"},
        "from_time": {"type": "string", "description": "HHMM"},
        "to_time": {"type": "string", "description": "HHMM"},
        "destination": {"type": "string", "description": "도착 공항 IATA 코드"}},
        "required": ["airport"]},
    func=search_departures))

register(Tool(
    name="search_arrivals",
    description="공항의 도착 운항 시간표 조회. 날짜(YYYYMMDD)를 생략하면 오늘. 출발지 IATA 코드로 거를 수 있다.",
    parameters={"type": "object", "properties": {
        "airport": {"type": "string", "description": "도착 공항 IATA 코드 (ICN, GMP 등)"},
        "date": {"type": "string", "description": "YYYYMMDD"},
        "from_time": {"type": "string", "description": "HHMM"},
        "to_time": {"type": "string", "description": "HHMM"},
        "origin": {"type": "string", "description": "출발 공항 IATA 코드"}},
        "required": ["airport"]},
    func=search_arrivals))

register(Tool(
    name="search_flight",
    description="편명(KE123 등)으로 운항을 찾을 때 쓴다. 공항을 몰라도 인천·한국공항공사 공항 전체에서 찾는다.",
    parameters={"type": "object", "properties": {
        "flight_no": {"type": "string", "description": "예: KE123"},
        "airport": {"type": "string", "description": "공항을 알 때만 IATA 코드 (모르면 생략)"},
        "date": {"type": "string", "description": "YYYYMMDD"}},
        "required": ["flight_no"]},
    func=search_flight))

register(Tool(
    name="get_flight_detail",
    description="후보 항공편의 편명으로 상세(탑승구·터미널·상태·예상 시각)를 확인할 때 쓴다. "
                "출발 목록에 탑승구가 없을 때(한국공항공사 공항) 특히 필요하다.",
    parameters={"type": "object", "properties": {
        "airport": {"type": "string", "description": "기준 공항 IATA 코드"},
        "flight_no": {"type": "string", "description": "예: KE123"},
        "date": {"type": "string", "description": "YYYYMMDD"},
        "direction": {"type": "string", "enum": ["departure", "arrival"]}},
        "required": ["airport", "flight_no"]},
    func=get_flight_detail))
