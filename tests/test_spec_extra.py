"""명세서·셀프 체크리스트 대조 중 보완한 동작 테스트 (네트워크·키 불필요)."""
import pytest

from app import config
from app.agent import graph, nodes
from app.events import EventBus
from app.tools import facilities, flights, registry
from tests.test_agent import TODAY, fake_tools, noop


def fake_fetch(items_by_url, seen=None):
    def _fetch(url, params, cache=True, ttl=None):
        if seen is not None:
            seen.append(dict(params))
        return {"items": items_by_url.get(url, []), "code": "00", "total": len(items_by_url.get(url, []))}
    return _fetch


# ---------------- F3002: 기본값과 요청·조회 기준이 다를 때 ----------------
def test_date_defaults_to_today_and_in_range_is_kept():
    assert flights.resolve_date(None) == ("20261119", "20261119", None)       # 기본값 = 오늘
    assert flights.resolve_date("20261125") == ("20261125", "20261125", None)  # +6일까지는 그대로
    assert flights.resolve_date("20261116")[2] is None                          # −3일까지는 그대로


def test_future_date_out_of_range_uses_same_weekday_and_says_so():
    got, requested, note = flights.resolve_date("20261224")         # 2026-12-24 목요일
    assert requested == "20261224"
    assert got == "20261119"                                         # 범위 안의 목요일
    assert "추정" in note and "2026-12-24(목)" in note and "2026-11-19(목)" in note


def test_search_reports_requested_and_queried_dates(monkeypatch):
    seen = []
    items = [{"flightId": "KE1", "airportCode": "NRT", "scheduleDateTime": "2026-11-23 09:00"}]
    monkeypatch.setattr(flights, "fetch", fake_fetch({flights.IIAC_PAX_DEP: items}, seen))
    res = flights.search_departures("ICN", date="20261228")          # 월요일, 범위 밖
    assert res["requested_date"] == "20261228" and res["date"] == "20261123"
    assert res["date_estimated"] is True and "추정" in res["note"]
    assert seen[0]["searchday"] == "20261123" and seen[0]["from_time"] == "0000"   # 추정 조회는 하루 전체


def test_bad_date_is_rejected_as_bad_args():
    res = registry.call("search_departures", airport="ICN", date="20261345")
    assert res["ok"] is False and res["error"]["code"] == "BAD_ARGS"


# ---------------- F3003: 판단에 필요한 값이 비면 판단하지 않는다 ----------------
def test_flight_without_time_is_not_judged_or_recommended():
    rows = [{"flight_no": "XX1", "scheduled": None, "estimated": None, "destination_code": "NRT", "direction": "departure"},
            {"flight_no": "KE2", "scheduled": "2026-11-19 15:00", "destination_code": "NRT", "direction": "departure"}]
    j = nodes.evaluate_flights({"flights": rows}, {"earliest": True},
                               {"direction": "departure", "other_codes": ["NRT"], "base": "ICN", "date": TODAY})
    assert j["chosen"]["flight_no"] == "KE2"
    assert j["rejected"] == [{"flight_no": "XX1", "airline": None, "time": "-", "reason": "출발 시각 정보 없음(판단하지 않음)"}]


# ---------------- F3004: 보조 정보의 이름·위치·링크, 결과 없음 형식 ----------------
def test_facilities_give_name_location_link_and_empty_format(monkeypatch):
    items = [{"entrpskoreannm": "온누리약국", "lckoreannm": "제1여객터미널 3층", "servicetime": "06:00~22:00", "arrordep": "D"}]
    monkeypatch.setattr(facilities, "fetch", fake_fetch({facilities.URL: items}))
    hit = facilities.get_incheon_facilities(keyword="약국")
    row = hit["rows"][0]
    assert row["name"] == "온누리약국" and row["location"] and row["map_url"].startswith("https://www.google.com/maps")
    none = facilities.get_incheon_facilities(keyword="스케이트장")
    assert none["count"] == 0 and none["empty"] is True and none["note"].startswith("결과 없음")


# ---------------- F3005: 기준·선호·제약, 말하지 않은 조건은 비움 ----------------
def test_conditions_are_grouped_and_missing_ones_stay_empty():
    groups, empty = nodes.group_conditions({"date": "20261120", "from_time": "1500", "destination": "CJU",
                                            "arrive_airport_at": "1300", "buffer_min": None, "earliest": False})
    assert groups["기준"] == {"date": "20261120", "from_time": "1500"}
    assert groups["선호"] == {"destination": "CJU"}
    assert groups["제약"] == {"arrive_airport_at": "1300"}
    assert "buffer_min" in empty and "airport" in empty and "earliest" in empty


def test_intent_event_carries_groups(monkeypatch):
    fake_tools(monkeypatch, TODAY)
    trace = []
    graph.run_agent("김포에서 제주 내일 오후 3시 이후", "g1", trace)
    ev = next(e for e in trace if e["event"] == "intent_analyzed")["payload"]
    assert ev["groups"]["기준"]["from_time"] == "1500" and ev["groups"]["선호"]["destination"] == "CJU"
    assert "arrive_airport_at" in ev["empty"]


# ---------------- F3006: 그래프 구조 출력 ----------------
def test_graph_structure_lists_design_stages():
    text = graph.structure()
    for stage in graph.STAGES:
        assert stage in text


# ---------------- F3007 · NF3003 · F3019: 도구 하나를 일부러 실패시키는 스위치 ----------------
def test_forced_tool_failure_keeps_agent_running(monkeypatch):
    fake_tools(monkeypatch, TODAY)
    monkeypatch.setattr(config, "VERIFY_FAIL_TOOLS", {"get_incheon_passenger_forecast"})
    real = nodes.call

    def through_switch(tool, **args):           # 가짜 도구 앞에 실제 스위치 판단을 둔다
        if tool in config.VERIFY_FAIL_TOOLS:
            return registry.call(tool, **args)
        return real(tool, **args)
    monkeypatch.setattr(nodes, "call", through_switch)
    trace = []
    res = graph.run_agent("빠른 시일내에 센다이에 가고 싶어", "f1", trace)
    failed = [e for e in trace if e["event"] == "tool_result" and not e["payload"]["ok"]]
    assert failed and failed[0]["payload"]["tool"] == "get_incheon_passenger_forecast"
    assert failed[0]["payload"]["code"] == "NETWORK"
    assert trace[-1]["event"] == "memory_updated" and res["answer"]        # 실패해도 끝까지 답한다
    assert "보조 정보" in res["answer"] or "OZ152" in res["answer"]


def test_registry_switch_returns_failure_envelope(monkeypatch):
    monkeypatch.setattr(config, "VERIFY_FAIL_TOOLS", {"search_airports"})
    res = registry.call("search_airports", query="김포")
    assert res == {"ok": False, "error": {"code": "NETWORK",
                                          "message": "요청 실패: 검증을 위해 일부러 실패시킨 도구입니다 (VERIFY_FAIL_TOOLS)."}}


# ---------------- F3012 · F3013: 로깅 Agent 없이, 저장이 실패해도 ----------------
def test_bus_without_subscriber_and_with_broken_subscriber():
    bus = EventBus()
    assert bus.publish("x", "s", a=1)["event"] == "x"            # 구독자 없음
    def broken(msg):
        raise RuntimeError("disk full")
    bus.subscribe(broken)
    assert bus.publish("y", "s")["event"] == "y"                   # 구독자가 예외를 내도 발행은 계속
    bus.unsubscribe(broken)
    assert bus.subscriber_count == 0


def test_attach_logger_modes(monkeypatch, tmp_path):
    from app import logger_agent
    from app.logger_agent import AgentLogger
    fresh = EventBus()
    monkeypatch.setattr(logger_agent, "bus", fresh)
    monkeypatch.setattr(logger_agent, "_attached", None)
    logger_agent.attach_logger(AgentLogger(db_path=str(tmp_path / "a.sqlite3")), mode="off")
    assert fresh.subscriber_count == 0
    monkeypatch.setattr(logger_agent, "_attached", None)
    logger_agent.attach_logger(AgentLogger(db_path=str(tmp_path / "b.sqlite3")), mode="broken")
    assert fresh.subscriber_count == 1 and fresh.publish("z", "s")["event"] == "z"


# ---------------- F3011: 제외한 후보와 사유 ----------------
def test_answer_lists_excluded_flights_with_reason(monkeypatch):
    from app.agent.llm import KST
    from datetime import datetime

    def fake(tool, **args):
        if tool == "search_departures":
            rows = [{"flight_no": "KE721", "airline": "대한항공", "scheduled": "2026-11-19 09:30", "destination_code": "KIX",
                     "destination_name": "오사카", "direction": "departure", "provider": "IIAC", "gate": "10"},
                    {"flight_no": "OZ112", "airline": "아시아나", "scheduled": "2026-11-19 12:00", "destination_code": "KIX",
                     "destination_name": "오사카", "direction": "departure", "provider": "IIAC", "gate": "20"}]
            return {"ok": True, "data": {"airport": "ICN", "airport_name": "인천", "date": args["date"], "provider": "IIAC",
                                         "count": 2, "flights": rows, "note": None}}
        return {"ok": True, "data": {"count": 0, "rows": [], "current_hour": 8, "selectdate": "0"}}
    monkeypatch.setattr(nodes, "call", fake)
    monkeypatch.setattr(nodes, "_today", lambda: TODAY)
    monkeypatch.setattr(nodes, "now_kst", lambda: datetime(2026, 11, 19, 7, 0, tzinfo=KST))
    trace = []
    res = graph.run_agent("인천공항에 9시 도착하는데 탈 수 있는 오사카행", "x1", trace)
    assert "추천: 아시아나 OZ112" in res["answer"]
    assert "제외한 후보: 준비 시간 부족 1편(KE721 09:30)" in res["answer"]
    assert "판단 기준: 공항 도착 09:00 + 준비 120분 = 11:00 이후 출발하는 편" in res["answer"]
    assert "늦어도 공항 도착 10:00" in res["answer"] and "체크인 마감(추정)" in res["answer"]
    judged = next(e for e in trace if e["event"] == "judged")["payload"]
    assert judged["rejected_reasons"] == {"준비 시간 부족": 1}


# ---------------- F3014 · F3017: API 경로와 응답 형식 ----------------
def test_openapi_has_separate_paths_and_no_log_update():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from app.main import app
    spec = TestClient(app).get("/openapi.json").json()
    paths = spec["paths"]
    assert "post" in paths["/api/chat"] and "get" in paths["/api/plans"] and "get" in paths["/api/logs"]
    assert set(paths["/api/logs/{sid}"]) == {"get", "delete"}                    # 수정 API 없음
    for p in paths:
        if p.startswith("/api/logs"):
            assert not {"put", "patch"} & set(paths[p])
    schemas = spec["components"]["schemas"]
    for name in ("ChatOut", "PlanSavedOut", "PlanListOut", "LogListOut", "LogDetailOut", "ErrorOut"):
        assert name in schemas


# ---------------- F3010: LLM 이 직전 값을 베껴 와도 이번 문장에 적힌 값으로 바꾼다 ----------------
def test_llm_copied_previous_airport_is_replaced_by_what_was_said():
    from app.agent.llm import Intent, normalize, reconcile, rule_intent
    prev = {"intent": "departures", "airport": "GMP", "destination": "CJU", "date": "20261120", "earliest": True}
    copied = normalize(Intent(intent="departures", airport="GMP", destination="CJU", date="20261120"))   # LLM 이 김포를 그대로 둠
    fixed, filled = reconcile(copied, rule_intent("김해에서는?", today="20261119"), prev)
    assert fixed.airport == "PUS" and fixed.destination == "CJU" and fixed.date == "20261120"
    assert "airport(이번 문장)" in filled
    ok, _ = reconcile(copied, rule_intent("김포에서 제주", today="20261119"), prev)               # 같은 값이면 그대로
    assert ok.airport == "GMP"


def test_llm_empty_fields_are_filled_from_the_sentence():
    from app.agent.llm import Intent, normalize, reconcile, rule_intent
    fixed, filled = reconcile(normalize(Intent(intent="departures")), rule_intent("대구에서 인천 가는 가장 빠른 항공편", today="20261119"))
    assert (fixed.airport, fixed.destination) == ("TAE", "ICN")
