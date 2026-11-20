"""운항 정규화 테스트. fetch 를 가짜 응답으로 바꿔서 실행한다."""
import pytest

from app.tools import flights


def fake_fetch(items_by_url):
    def _fetch(url, params, cache=True):
        return {"items": items_by_url[url], "code": "00"}
    return _fetch


def test_iiac_departures_normalized(monkeypatch):
    items = [{"flightId": "KE123", "airline": "대한항공", "airportCode": "NRT", "airport": "도쿄(나리타)",
              "scheduleDateTime": "2026-11-20 09:30", "estimatedDatetime": "2026-11-20 09:45",
              "remark": "출발", "terminalId": "P01", "gateNumber": "101"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}))
    res = flights.search_departures("icn", date="20261120")
    row = res["flights"][0]
    assert res["provider"] == "IIAC" and res["airport"] == "ICN"
    assert row["flight_no"] == "KE123"
    assert row["destination_code"] == "NRT"
    assert row["estimated"] == "2026-11-20 09:45"
    assert row["gate"] == "101"
    assert "maps" in row["map_url"]


def test_iiac_field_case_difference_absorbed(monkeypatch):
    items = [{"flightid": "OZ701", "estimatedDateTime": "2026-11-20 10:00", "scheduleDateTime": "2026-11-20 09:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}))
    row = flights.search_departures("ICN", date="20261120")["flights"][0]
    assert row["flight_no"] == "OZ701"
    assert row["estimated"] == "2026-11-20 10:00"


def test_kac_departures_use_arrival_alias(monkeypatch):
    items = [{"fid": "7C101", "airline": "제주항공", "arrAirportCode": "KIX", "arrAirport": "오사카(간사이)",
              "scheduledatetime": "2026-11-20 08:00", "estimateddatetime": "2026-11-20 08:20", "rmkKor": "지연"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.KAC_DEP: items}))
    res = flights.search_departures("GMP", date="20261120")
    row = res["flights"][0]
    assert res["provider"] == "KAC"
    assert row["destination_code"] == "KIX"
    assert row["gate"] is None          # KAC 출발 목록에는 탑승구가 없다


def test_destination_filter_applied_client_side(monkeypatch):
    items = [{"flightId": "A1", "airportCode": "NRT", "scheduleDateTime": "2026-11-20 09:00"},
             {"flightId": "A2", "airportCode": "HND", "scheduleDateTime": "2026-11-20 10:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}))
    res = flights.search_departures("ICN", date="20261120", destination="hnd")
    assert [f["flight_no"] for f in res["flights"]] == ["A2"]


def test_empty_result_has_note(monkeypatch):
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: []}))
    res = flights.search_departures("ICN", date="20261120")
    assert res["count"] == 0 and res["note"]


def test_icn_detail_reads_day_list_by_flight_number(monkeypatch):
    items = [{"flightId": "KE 123", "airportCode": "NRT", "scheduleDateTime": "2026-11-20 09:30",
              "gatenumber": "12", "terminalid": "P01", "remark": "탑승중"},
             {"flightId": "OZ1", "airportCode": "HND", "scheduleDateTime": "2026-11-20 10:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}))
    res = flights.get_flight_detail("ICN", "ke123", date="20261120")
    assert res["count"] == 1 and res["rows"][0]["gate"] == "12"


def test_kac_detail_today_uses_realtime_info_with_gate(monkeypatch):
    def _fetch(url, params, cache=True):
        assert url == flights.KAC_INFO
        if params["schLineType"] != "D":
            return {"items": []}
        return {"items": [{"airFln": "KE1234", "std": "0930", "etd": "0945", "gate": "A12", "rmkKor": "탑승중",
                           "arrivedKor": "제주"},
                          {"airFln": "OZ1", "std": "1000", "gate": "C1"}]}

    monkeypatch.setattr(flights, "fetch", _fetch)
    monkeypatch.setattr(flights, "today_kst", lambda: "20261119")
    res = flights.get_flight_detail("GMP", "ke 1234", date="20261119")
    row = res["rows"][0]
    assert res["count"] == 1 and row["gate"] == "A12" and row["line"] == "국내"
    assert row["scheduled"] == "2026-11-19 09:30" and row["estimated"] == "2026-11-19 09:45"
    assert row["destination_name"] == "제주"


def test_kac_detail_other_day_falls_back_to_list(monkeypatch):
    items = [{"flightid": "7C101", "arrAirportCode": "KIX", "scheduledatetime": "2026-11-21 08:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.KAC_DEP: items}))
    monkeypatch.setattr(flights, "today_kst", lambda: "20261119")
    res = flights.get_flight_detail("GMP", "7C101", date="20261121")
    assert res["count"] == 1 and "목록" in res["source"]


@pytest.mark.parametrize("bad", ["ZZZ", "", None])
def test_bad_airport_rejected(bad):
    with pytest.raises(ValueError):
        flights.search_departures(bad, date="20261120")


def test_bad_date_rejected():
    with pytest.raises(ValueError):
        flights.search_departures("ICN", date="2026-11-20")


def test_icn_departures_call_passenger_service(monkeypatch):
    seen = {}

    def _fetch(url, params, cache=True):
        seen.update(url=url, params=params)
        return {"items": [{"flightId": "KE1", "airportCode": "NRT", "airport": "도쿄",
                           "scheduleDateTime": "2026-11-20 09:00",
                           "gatenumber": "12", "terminalid": "P01"}]}

    monkeypatch.setattr(flights, "fetch", _fetch)
    res = flights.search_departures("ICN", date="20261120", destination="nrt")
    assert "StatusOfPassengerFlightsDeOdp/getPassengerDeparturesDeOdp" in seen["url"]
    assert seen["params"]["searchday"] == "20261120"
    assert seen["params"]["airport_code"] == "NRT"
    row = res["flights"][0]
    assert row["gate"] == "12" and row["terminal"] == "P01"


def test_today_without_time_starts_from_now(monkeypatch):
    seen = {}

    def _fetch(url, params, cache=True):
        seen["params"] = params
        return {"items": []}

    monkeypatch.setattr(flights, "fetch", _fetch)
    monkeypatch.setattr(flights, "today_kst", lambda: "20261119")
    monkeypatch.setattr(flights, "_now_hhmm", lambda: "1430")
    flights.search_departures("GMP", date="20261119")
    assert seen["params"]["from_time"] == "1430"


def test_other_day_without_time_starts_from_midnight(monkeypatch):
    seen = {}

    def _fetch(url, params, cache=True):
        seen["params"] = params
        return {"items": []}

    monkeypatch.setattr(flights, "fetch", _fetch)
    monkeypatch.setattr(flights, "today_kst", lambda: "20261119")
    monkeypatch.setattr(flights, "_now_hhmm", lambda: "1430")
    flights.search_departures("GMP", date="20261120")
    assert seen["params"]["from_time"] == "0000"


def test_icn_arrivals_use_arrival_service_and_origin_fields(monkeypatch):
    items = [{"flightId": "LJ5264", "airline": "진에어", "airportCode": "NRT", "airport": "도쿄/나리타",
              "scheduleDateTime": "2026-11-20 09:00", "estimatedDateTime": "2026-11-20 09:10",
              "gatenumber": "211", "terminalid": "T1"}]
    seen = {}

    def _fetch(url, params, cache=True):
        seen.update(url=url, params=params)
        return {"items": items}

    monkeypatch.setattr(flights, "fetch", _fetch)
    res = flights.search_arrivals("ICN", date="20261120")
    assert "getPassengerArrivalsDeOdp" in seen["url"]
    row = res["flights"][0]
    assert row["direction"] == "arrival"
    assert row["origin_code"] == "NRT" and row["origin_name"] == "도쿄/나리타"
    assert row["gate"] == "211" and row["terminal"] == "T1"


def test_kac_arrivals_read_departure_airport_code(monkeypatch):
    items = [{"flightid": "7C101", "airline": "제주항공", "depAirportCode": "KIX",
              "scheduledatetime": "2026-11-20 08:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.KAC_ARR: items}))
    res = flights.search_arrivals("GMP", date="20261120", origin="kix")
    assert res["flights"][0]["origin_code"] == "KIX"
    assert res["provider"] == "KAC"


def test_flight_search_matches_number_ignoring_spaces(monkeypatch):
    dep = [{"flightId": "KE 123", "airportCode": "NRT", "scheduleDateTime": "2026-11-20 09:00"},
           {"flightId": "KE999", "airportCode": "HND", "scheduleDateTime": "2026-11-20 10:00"}]
    arr = [{"flightId": "KE123", "airportCode": "NRT", "scheduleDateTime": "2026-11-20 18:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: dep, flights.IIAC_PAX_ARR: arr}))
    res = flights.search_flight("ke123", airport="ICN", date="20261120")
    assert res["count"] == 2
    assert [f["direction"] for f in res["flights"]] == ["departure", "arrival"]


def test_flight_not_found_has_note(monkeypatch):
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: [], flights.IIAC_PAX_ARR: []}))
    res = flights.search_flight("ZZ1", airport="ICN", date="20261120")
    assert res["count"] == 0 and res["note"]


def test_pages_are_fetched_until_total_reached(monkeypatch):
    calls = []

    def _fetch(url, params, cache=True):
        calls.append(params["pageNo"])
        start = (params["pageNo"] - 1) * params["numOfRows"]
        batch = [{"flightId": f"A{n}", "airportCode": "NRT", "scheduleDateTime": "2026-11-20 09:00"}
                 for n in range(start, min(start + params["numOfRows"], 1500))]
        return {"items": batch, "total": 1500}

    monkeypatch.setattr(flights, "fetch", _fetch)
    res = flights.search_departures("ICN", date="20261120")
    assert calls == [1, 2]                  # 1000 + 500 = 1500, 두 번만 받는다
    assert res["count"] == 1500 and res["total"] == 1500


def test_codeshares_are_merged_into_one_card(monkeypatch):
    items = [{"flightId": "OZ152", "airline": "아시아나항공", "airportCode": "SDJ", "scheduleDateTime": "2026-11-21 09:35",
              "terminalid": "P03"},
             {"flightId": "NH6982", "airline": "전일본공수", "airportCode": "SDJ", "scheduleDateTime": "2026-11-21 09:35",
              "terminalid": "P03"},
             {"flightId": "KE5491", "airline": "대한항공", "airportCode": "SDJ", "scheduleDateTime": "2026-11-21 09:35",
              "terminalid": "P03"},
             {"flightId": "LJ1", "airline": "진에어", "airportCode": "SDJ", "scheduleDateTime": "2026-11-21 09:35",
              "terminalid": "P02"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}))
    res = flights.search_departures("ICN", date="20261121")
    assert [f["flight_no"] for f in res["flights"]] == ["OZ152", "LJ1"]
    assert res["flights"][0]["codeshares"] == ["NH6982", "KE5491"]


def test_codeshare_flag_from_api_is_followed(monkeypatch):
    items = [{"flightId": "KE5491", "codeshare": "Slave", "masterflightid": "OZ152", "airportCode": "SDJ",
              "scheduleDateTime": "2026-11-21 09:35"},
             {"flightId": "OZ152", "codeshare": "Master", "airportCode": "SDJ", "scheduleDateTime": "2026-11-21 09:35"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}))
    res = flights.search_departures("ICN", date="20261121")
    assert [f["flight_no"] for f in res["flights"]] == ["OZ152"] and res["flights"][0]["codeshares"] == ["KE5491"]


def test_flight_search_without_airport_checks_all_airports(monkeypatch):
    def board(code, ymd, f_from, f_to, direction, other=None):
        rows = []
        if code == "GMP" and direction == "departure":
            rows = [{"airport": "GMP", "direction": "departure", "flight_no": "7C101", "scheduled": "2026-11-20 08:00",
                     "destination_code": "CJU"}]
        if code == "CJU" and direction == "arrival":
            rows = [{"airport": "CJU", "direction": "arrival", "flight_no": "7C101", "scheduled": "2026-11-20 09:05",
                     "origin_code": "GMP"}]
        return rows, len(rows), len(rows)

    monkeypatch.setattr(flights, "_board", board)
    monkeypatch.setattr(flights, "_kac_info", lambda *a: [])
    res = flights.search_flight("7c 101", date="20261120")
    assert res["searched"] == 15 and res["airport"] == "GMP"
    assert [(f["airport"], f["direction"]) for f in res["flights"]] == [("GMP", "departure")]   # 같은 비행의 도착 줄은 뺀다


def test_large_page_failure_retries_smaller_pages(monkeypatch):
    from app.tools.common import ApiError
    sizes = []

    def fake(url, params, cache=True):
        sizes.append(params["numOfRows"])
        if params["numOfRows"] > 50:
            raise ApiError("PROVIDER", "제공기관 결과코드 04")
        return {"items": [{"flightid": "A1", "scheduledatetime": "2026-11-20 08:00"}], "total": 1}

    monkeypatch.setattr(flights, "fetch", fake)
    res = flights.search_departures("GMP", date="20261120")
    assert sizes == [100, 50] and res["count"] == 1


def test_flight_search_attaches_arrival_time(monkeypatch):
    dep = {"airport": "GMP", "direction": "departure", "destination_code": "CJU", "scheduled": "2026-11-20 08:50",
           "estimated": "2026-11-20 09:03", "flight_no": "KE1041"}
    arr = {"airport": "CJU", "direction": "arrival", "origin_code": "GMP", "scheduled": "2026-11-20 09:55",
           "estimated": "2026-11-20 10:08", "flight_no": "KE1041"}
    monkeypatch.setattr(flights, "today_kst", lambda: "20261120")
    monkeypatch.setattr(flights, "_match_airport",
                        lambda c, no, ymd, errors=None: [dict(dep)] if c == "GMP" else ([dict(arr)] if c == "CJU" else []))
    monkeypatch.setattr(flights, "_kac_info", lambda *a: [{"gate": "7"}])
    res = flights.search_flight("KE1041", date="20261120")
    assert res["count"] == 1
    row = res["flights"][0]
    assert row["direction"] == "departure"
    assert row["arr_scheduled"] == "2026-11-20 09:55" and row["arr_estimated"] == "2026-11-20 10:08"


def test_flight_search_finds_next_day_arrival_and_keeps_both_legs(monkeypatch):
    dep = {"airport": "GMP", "direction": "departure", "destination_code": "CJU", "scheduled": "2026-11-20 23:30",
           "flight_no": "7C199", "gate": None}
    arr = {"airport": "CJU", "direction": "arrival", "origin_code": "GMP", "scheduled": "2026-11-21 00:35",
           "flight_no": "7C199", "gate": None}

    monkeypatch.setattr(flights, "today_kst", lambda: "20261120")

    def match(c, no, ymd, errors=None):
        if c == "GMP" and ymd == "20261120":
            return [dict(dep)]
        if c == "CJU" and ymd == "20261121":
            return [dict(arr)]
        return []
    monkeypatch.setattr(flights, "_match_airport", match)
    monkeypatch.setattr(flights, "_kac_info", lambda *a: [])
    res = flights.search_flight("7C199", date="20261120")
    row = res["flights"][0]
    assert res["count"] == 1
    assert row["legs"]["departure"]["scheduled"] == "2026-11-20 23:30"
    assert row["legs"]["arrival"]["scheduled"] == "2026-11-21 00:35"


def test_flight_search_overseas_side_is_empty(monkeypatch):
    dep = {"airport": "ICN", "direction": "departure", "destination_code": "SEA", "scheduled": "2026-11-20 16:10",
           "flight_no": "KE41", "gate": "224", "terminal": "P03"}
    monkeypatch.setattr(flights, "_match_airport", lambda c, no, ymd, errors=None: [dict(dep)] if c == "ICN" else [])
    res = flights.search_flight("KE041", date="20261120")
    row = res["flights"][0]
    assert row["legs"]["departure"]["gate"] == "224"
    assert row["legs"]["arrival"] is None
