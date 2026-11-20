"""시설 안내 · 승객 예고 · 공항 개요 도구 테스트 (가짜 응답, 네트워크 없음)."""
from app.tools import airport_info, facilities, passenger, registry


def test_classify_and_location_parsing():
    assert facilities.classify("신라면세점", "화장품") == "면세점"
    assert facilities.classify("파리바게뜨", "베이커리") == "식당·카페"
    assert facilities.classify("화장실", None) == "편의시설"
    assert facilities.classify("하나은행 환전소", None) == "환전·은행"
    assert facilities.parse_location("탑승동 3층 동편 107번 탑승구 부근") == ("탑승동", "3F")
    assert facilities.parse_location("제1여객터미널 지하1층") == ("T1", "B1")
    assert facilities.parse_location("위치 미상") == (None, None)


def test_facilities_filters_and_summary(monkeypatch):
    items = [
        {"entrpskoreannm": "신라면세점", "trtmntprdlstkoreannm": "화장품", "lckoreannm": "제1여객터미널 3층", "arrordep": "D"},
        {"entrpskoreannm": "온누리약국", "lckoreannm": "제1여객터미널 1층", "arrordep": "A", "servicetime": "06:00~22:00"},
        {"entrpskoreannm": "스타벅스", "trtmntprdlstkoreannm": "커피", "lckoreannm": "탑승동 3층", "arrordep": "D"},
    ]
    calls = []

    def fake(url, params, cache=True, ttl=None):
        calls.append((params["pageNo"], ttl))
        return {"items": items if params["pageNo"] == 1 else [], "total": 3}

    monkeypatch.setattr(facilities, "fetch", fake)
    res = facilities.get_incheon_facilities(area="D")
    assert res["count"] == 2 and {r["category"] for r in res["rows"]} == {"면세점", "식당·카페"}
    assert res["summary"]["by_place"]["T1|3F"] == {"면세점": 1}
    assert calls == [(1, facilities.TTL)]                    # total 을 다 받으면 더 부르지 않는다
    assert facilities.get_incheon_facilities(keyword="약국")["rows"][0]["hours"] == "06:00~22:00"


def test_passenger_forecast_sums_terminals_and_ratio(monkeypatch):
    items = [{"adate": "20261119", "atime": "07_08", "t1dgsum1": "100", "t1dgsum2": "50", "t1egsum1": "30", "t2dgsum1": "40"},
             {"adate": "20261119", "atime": "08_09", "t1dgsum1": "300", "t2dgsum1": "80"},
             {"adate": "20261119", "atime": "합계", "t1dgsum1": "450"}]
    monkeypatch.setattr(passenger, "fetch", lambda url, params, cache=True: {"items": items})
    res = passenger.get_incheon_passenger_forecast("1")
    first = res["rows"][0]
    assert first["hour"] == 7 and first["t1_departure"] == 150 and first["t1_arrival"] == 30
    assert res["peak"]["t1_departure"] == 300 and first["ratio"]["t1_departure"] == 0.5
    assert res["rows"][2]["hour"] is None                    # 합계 행은 비교에서 뺀다
    assert res["current_hour"] is None                       # 내일 자료에는 '지금'이 없다


def test_overview_collects_parts_and_keeps_partial_failures(monkeypatch):
    def fake_call(name, **kw):
        if name == "get_commercial_facilities":
            return {"ok": False, "error": {"code": "PROVIDER", "message": "x"}}
        return {"ok": True, "data": {"count": 0, "rows": []}}

    monkeypatch.setattr(registry, "call", fake_call)
    res = airport_info.get_airport_overview("gmp")
    assert res["has_congestion"] and set(res["parts"]) == {"congestion", "hourly", "commercial"}
    assert res["parts"]["commercial"]["ok"] is False
    icn = airport_info.get_airport_overview("ICN")
    assert set(icn["parts"]) == {"passenger", "facilities"}


def test_tools_are_marked_external_or_local():
    kinds = {t["name"]: t["kind"] for t in registry.catalog()}
    assert kinds["search_departures"] == "external"
    assert kinds["get_airport_overview"] == "local" and kinds["search_airports"] == "local"


def test_congestion_reads_realtime_snapshot_with_time_label(monkeypatch):
    from app.tools import congestion
    items = [{"IATA_APCD": "GMP", "PRC_HR": "15:50", "CGDR_ALL_LVL": "1", "CGDR_A_LVL": "1", "CGDR_B_LVL": "2",
              "CGDR_C_LVL": "1", "WAIT_TM": "7"},
             {"IATA_APCD": "CJU", "PRC_HR": "15:50", "CGDR_ALL_LVL": "3"}]
    monkeypatch.setattr(congestion, "fetch", lambda url, params, cache=True: {"items": items})
    res = congestion.get_airport_congestion("gmp")
    row = res["rows"][0]
    assert res["count"] == 1 and row["time"] == "15:50" and row["level_b"] == "2"
    assert row["extra"] == {"WAIT_TM": "7"}                  # 이름 모르는 항목도 넘긴다


def test_hourly_traffic_falls_back_to_flight_counts(monkeypatch):
    from app.tools import traffic
    from app.tools.common import ApiError

    def fake_fetch(url, params, cache=True, ttl=None):
        raise ApiError("PORTAL", "활용신청 내역이 없거나 승인 전")

    def board(code, date=None, from_time=None, to_time=None, **kw):
        return {"count": 2, "flights": [{"scheduled": "2026-11-19 09:10", "estimated": None},
                                        {"scheduled": "2026-11-19 09:40", "estimated": "2026-11-19 10:05"}]}

    monkeypatch.setattr(traffic, "fetch", fake_fetch)
    monkeypatch.setattr(traffic.flights, "search_departures", board)
    monkeypatch.setattr(traffic.flights, "search_arrivals", lambda *a, **k: {"count": 0, "flights": []})
    res = traffic.get_hourly_traffic("PUS", date="20261119")
    assert res["source"] == "flights" and res["unit"] == "편" and "승인" in res["fallback_reason"]
    assert res["rows"][9]["departure"] == 1 and res["rows"][10]["departure"] == 1   # 예상 시각 기준


def test_hourly_traffic_uses_expected_passengers_when_available(monkeypatch):
    from app.tools import traffic
    items = [{"airport": "PUS", "date": "20261119", "hour": "08", "pax": "300"},
             {"airport": "PUS", "date": "20261119", "hour": "09", "pax": "450"}]
    monkeypatch.setattr(traffic, "fetch", lambda url, params, cache=True, ttl=None: {"items": items})
    res = traffic.get_hourly_traffic("PUS", date="20261119")
    assert res["source"] == "expected_passengers" and [r["value"] for r in res["rows"]] == [300, 450]


def test_gateway_json_error_is_readable():
    from app.tools import common
    text = '{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"returnReasonCode":"04","returnAuthMsg":"HTTP_ERROR"}}}'
    parsed = common.parse(text.encode(), text)
    assert parsed["code"] == "04" and parsed["msg"] == "HTTP_ERROR"


def test_lease_contract_filters_airport_active_and_business(monkeypatch):
    from app.tools import commercial
    items = [
        {"storeName": "스타벅스", "airportCode": "GMP", "leaseLocation": "국제선 격리 3층", "typeOfBusiness": "식음료업",
         "contractEndDate": "2028-12-08", "contractStatus": "진행중"},
        {"storeName": "와우로지스틱스", "airportCode": "GMP", "leaseLocation": "하늘길 210 구화물청사",
         "typeOfBusiness": "화물창고업", "contractEndDate": "2026-12-31", "contractStatus": "진행중"},
        {"storeName": "옛날가게", "airportCode": "GMP", "leaseLocation": "국내선 1층", "typeOfBusiness": "판매업",
         "contractEndDate": "2020-01-01", "contractStatus": "종료"},
        {"storeName": "파라다이스 라운지", "airportCode": "GMP", "leaseLocation": "국제선 1층 서편", "typeOfBusiness": "안내시설",
         "contractEndDate": "2029-06-26", "contractStatus": "진행중"},
    ]
    seen = {}

    def fake(url, params, cache=True, ttl=None):
        seen.update(url=url, params=params)
        return {"items": items, "total": 4}

    monkeypatch.setattr(commercial, "fetch", fake)
    monkeypatch.setattr(commercial, "today_kst", lambda: "20261119")
    res = commercial.get_commercial_facilities("gmp")
    assert "airport-lease-contract" in seen["url"] and seen["params"]["schAirportCode"] == "GMP"
    assert [r["name"] for r in res["rows"]] == ["스타벅스", "파라다이스 라운지"] and res["hidden_business"] == 1
    assert res["rows"][0]["zone"] == "국제선" and res["rows"][0]["floor"] == "3F" and res["rows"][0]["category"] == "식당·카페"
    assert res["summary"]["by_place"]["국제선|1F"] == {"라운지·휴식": 1}
    assert [r["name"] for r in commercial.get_commercial_facilities("GMP", keyword="식당")["rows"]] == ["스타벅스"]


def test_lease_contract_rejects_incheon():
    import pytest
    from app.tools import commercial
    with pytest.raises(ValueError):
        commercial.get_commercial_facilities("ICN")


def test_expected_passenger_path_failure_is_remembered(monkeypatch):
    from app.tools import traffic
    from app.tools.common import ApiError
    calls = []

    def fake(url, params, cache=True, ttl=None):
        calls.append(url)
        raise ApiError("HTTP", "HTTP 400 · 해당 오픈API 서비스가 없거나 폐기됨")

    monkeypatch.setattr(traffic, "fetch", fake)
    monkeypatch.setattr(traffic.flights, "search_departures", lambda *a, **k: {"count": 0, "flights": []})
    monkeypatch.setattr(traffic.flights, "search_arrivals", lambda *a, **k: {"count": 0, "flights": []})
    traffic.get_hourly_traffic("PUS", date="20261119")
    traffic.get_hourly_traffic("GMP", date="20261119")
    assert len(calls) == 3                                   # 주소 후보 3개를 한 번만 시험하고, 30분 동안 다시 부르지 않는다
