"""메인 에이전트 테스트: 요청 분석 · 맥락 병합 · 계획 · 제약 판단 · 재계획 · 응답 · 전체 흐름.
네트워크와 LLM 키 없이 실행된다 (도구 호출은 가짜 응답으로 바꾼다)."""
from datetime import datetime

import pytest

from app import config
from app.agent import graph, nodes
from app.agent.llm import Intent, normalize, rule_intent
from app.agent.memory import SessionMemory

TODAY = "20261119"


def noop(*a, **k):
    return None


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(config, "GMS_API_KEY", "")


# ---------------- 요청 분석 (F3005) ----------------
@pytest.mark.parametrize("text, kind, airport", [
    ("인천공항 출발 시간표 알려줘", "departures", "ICN"),
    ("김포 출발 편 보여줘", "departures", "GMP"),
    ("제주 혼잡도", "congestion", "CJU"),
    ("면세점 매장 알려줘", "facilities", None),
    ("인천 출입국 승객 예고", "passenger", "ICN"),
    ("안녕", "greeting", None),
    ("", "unknown", None),
])
def test_rule_intent_kinds(text, kind, airport):
    it = rule_intent(text, today=TODAY)
    assert it.intent == kind
    assert it.airport == airport


def test_sendai_request_is_departures_to_sdj():
    it = rule_intent("빠른 시일내에 센다이에 가고 싶어", today=TODAY)
    assert it.intent == "departures"
    assert it.destination == "SDJ" and it.airport is None      # 출발 공항은 지어내지 않는다
    assert it.earliest and it.flexible_date and it.date is None


def test_fastest_flight_to_city():
    it = rule_intent("센다이로 가는 가장 빠른 항공편", today=TODAY)
    assert (it.intent, it.destination, it.earliest) == ("departures", "SDJ", True)


def test_origin_destination_date_and_time():
    it = rule_intent("김포에서 제주 내일 오후 3시 이후", today=TODAY)
    assert (it.airport, it.destination, it.date, it.from_time) == ("GMP", "CJU", "20261120", "1500")


def test_airport_arrival_time_constraint():
    it = rule_intent("인천공항에 9시 도착하는데 탈 수 있는 오사카행", today=TODAY)
    assert it.airport == "ICN" and it.arrive_airport_at == "0900"
    assert "KIX" in it.destination


def test_flight_number_detail():
    it = rule_intent("김포 ke 123 게이트 알려줘", today=TODAY)
    assert (it.intent, it.flight_no, it.airport) == ("flight_detail", "KE123", "GMP")


def test_arrivals_with_origin():
    it = rule_intent("인천 도착하는 나리타발 항공편", today=TODAY)
    assert it.intent == "arrivals" and it.airport == "ICN" and it.origin == "NRT"


def test_normalize_drops_bad_values_instead_of_guessing():
    it = normalize(Intent(intent="weird", date="20261345", from_time="9999", flight_no="hello", buffer_min=9999))
    assert it.intent == "unknown"
    assert it.date is None and it.from_time is None and it.flight_no is None and it.buffer_min is None


# ---------------- 대화 맥락 (F3010) ----------------
PREV = {"intent": "departures", "airport": "ICN", "destination": "SDJ", "date": None, "earliest": True,
        "flexible_date": True, "flight_no": "OZ152"}


def test_followup_keeps_conditions_and_updates_only_changed():
    new = rule_intent("내일은?", today=TODAY).model_dump()
    merged, inherited = nodes.merge_context(new, PREV, "내일은?")
    assert merged["intent"] == "departures"
    assert merged["destination"] == "SDJ" and merged["airport"] == "ICN"
    assert merged["date"] == "20261120" and merged["flexible_date"] is False
    assert "intent" in inherited and "destination" in inherited


def test_followup_changes_origin_only():
    new = rule_intent("김포에서는?", today=TODAY).model_dump()
    merged, _ = nodes.merge_context(new, PREV, "김포에서는?")
    assert merged["airport"] == "GMP" and merged["destination"] == "SDJ"


def test_other_intent_borrows_airport():
    new = rule_intent("거기 혼잡도는?", today=TODAY).model_dump()
    merged, inherited = nodes.merge_context(new, {"intent": "departures", "airport": "GMP"}, "거기 혼잡도는?")
    assert merged["intent"] == "congestion" and merged["airport"] == "GMP" and inherited == ["airport"]


def test_session_memory_keeps_summary_only():
    m = SessionMemory()
    m.remember("s1", {"intent": "departures"}, "요약", "질문")
    assert m.get("s1")["turns"] == [{"user": "질문", "summary": "요약"}]
    assert m.forget("s1") and m.get("s1") is None


# ---------------- 계획 (F3006) ----------------
def intent(**kw):
    base = {"intent": "departures", "airport": None, "destination": None, "origin": None, "flight_no": None,
            "date": None, "flexible_date": False, "from_time": None, "to_time": None, "earliest": False,
            "arrive_airport_at": None, "buffer_min": None, "keyword": None}
    base.update(kw)
    return base


def test_plan_defaults_origin_and_says_so(monkeypatch):
    monkeypatch.setattr(nodes, "_today", lambda: TODAY)
    out = nodes.plan({"intent": intent(destination="SDJ", earliest=True), "replans": 0}, noop)
    first = out["steps"][0]
    assert first == {"tool": "search_departures", "args": {"airport": "ICN", "date": TODAY, "destination": "SDJ"},
                     "role": "candidates"}
    assert out["steps"][1]["tool"] == "get_incheon_passenger_forecast"      # 보조 정보 (F3004)
    assert any("인천" in a for a in out["assumptions"])


def test_plan_asks_when_nothing_to_search():
    out = nodes.plan({"intent": intent(), "replans": 0}, noop)
    assert out["steps"] == [] and "출발" in out["missing"]


def test_plan_without_tools_for_greeting():
    out = nodes.plan({"intent": intent(intent="greeting"), "replans": 0}, noop)
    assert out["steps"] == [] and out["missing"] is None


def test_replan_widens_time_first_then_next_day(monkeypatch):
    monkeypatch.setattr(nodes, "_today", lambda: TODAY)
    it = intent(airport="GMP", destination="CJU", from_time="1500")
    a1 = nodes.plan({"intent": it, "replans": 1}, noop)["steps"][0]["args"]
    a2 = nodes.plan({"intent": it, "replans": 2}, noop)["steps"][0]["args"]
    assert "from_time" not in a1 and a1["date"] == TODAY
    assert a2["date"] == "20261120"


# ---------------- 판단 (F3008 · F3009) ----------------
def flight(no, hhmm, remark="", date="2026-11-19", code="KIX"):
    return {"provider": "IIAC", "airport": "ICN", "direction": "departure", "airline": "항공", "flight_no": no,
            "scheduled": f"{date} {hhmm}", "estimated": None, "remark": remark, "gate": "10", "terminal": "P01",
            "destination_code": code, "destination_name": "오사카"}


def test_evaluate_applies_airport_arrival_and_buffer():
    it = intent(airport="ICN", destination="KIX", arrive_airport_at="0900", earliest=True)
    ctx = {"direction": "departure", "base": "ICN", "other_codes": ["KIX"], "date": TODAY}
    data = {"flights": [flight("A1", "09:30"), flight("A2", "11:30"), flight("A3", "12:00", "결항"),
                        flight("A4", "13:00", code="NRT")]}
    j = nodes.evaluate_flights(data, it, ctx, now=datetime(2026, 11, 19, 8, 0))
    assert [c["flight_no"] for c in j["candidates"]] == ["A2"]
    assert j["chosen"]["flight_no"] == "A2"
    assert {r["reason"] for r in j["rejected"]} == {"준비 시간 부족", "결항"}
    assert j["buffer"] == 120 and any("기본값" in n for n in j["notes"])     # 기본값을 썼다고 밝힌다


def test_judge_replans_until_cap_then_reports(monkeypatch):
    monkeypatch.setattr(config, "REPLAN_LIMIT", 3)
    it = intent(airport="ICN", destination="SDJ", earliest=True, flexible_date=True, from_time="2300")
    empty = {"tool": "search_departures", "role": "candidates", "ok": True, "data": {"flights": []}}
    decisions, state = [], {"intent": it, "replans": 0, "results": [empty],
                            "ctx": {"direction": "departure", "base": "ICN", "other_codes": ["SDJ"], "date": "20991231"}}
    for _ in range(6):
        out = nodes.judge(state, noop)
        decisions.append(out["decision"])
        if out["decision"] == "respond":
            break
        state = {**state, "replans": out["replans"], "replan_log": out["replan_log"]}
    assert decisions == ["replan", "replan", "replan", "respond"]
    assert out["judgement"]["limit_reached"] is True


def test_judge_does_not_retry_missing_key_but_retries_network_once():
    base = {"intent": intent(airport="ICN"), "ctx": {"direction": "departure"}, "replans": 0}
    no_key = {"tool": "search_departures", "role": "candidates", "ok": False, "error": {"code": "NO_KEY", "message": "x"}}
    net = {**no_key, "error": {"code": "NETWORK", "message": "x"}}
    assert nodes.judge({**base, "results": [no_key]}, noop)["decision"] == "respond"
    assert nodes.judge({**base, "results": [net]}, noop)["decision"] == "replan"
    assert nodes.judge({**base, "results": [net], "replans": 1}, noop)["decision"] == "respond"


def test_execute_reuses_results_on_replan(monkeypatch):
    calls = []
    monkeypatch.setattr(nodes, "call", lambda tool, **a: calls.append(tool) or {"ok": True, "data": {"count": 0}})
    steps = [{"tool": "get_airport_congestion", "args": {"airport": "GMP"}, "role": "support"}]
    first = nodes.execute({"steps": steps, "cache": {}}, noop)
    nodes.execute({"steps": steps, "cache": first["cache"]}, noop)
    assert calls == ["get_airport_congestion"]


def test_enrich_passes_chosen_flight_to_detail_tool(monkeypatch):
    seen = {}

    def fake(tool, **args):
        seen.update(tool=tool, args=args)
        return {"ok": True, "data": {"count": 1, "source": "KAC info",
                                     "rows": [{"gate": "7", "remark": "탑승중"}]}}

    monkeypatch.setattr(nodes, "call", fake)
    chosen = {**flight("7C101", "10:00"), "provider": "KAC", "gate": None}
    state = {"judgement": {"chosen": chosen, "candidates": [chosen]},
             "ctx": {"base": "GMP", "date": TODAY, "direction": "departure"}, "results": []}
    out = nodes.enrich(state, noop)
    assert seen == {"tool": "get_flight_detail",
                    "args": {"airport": "GMP", "flight_no": "7C101", "date": TODAY, "direction": "departure"}}
    assert out["judgement"]["chosen"]["gate"] == "7"


# ---------------- 전체 흐름 (재계획 + 맥락) ----------------
def fake_tools(monkeypatch, today):
    tomorrow = "20261120"

    def fake(tool, **args):
        if tool == "search_departures":
            rows = []
            if args["date"] == tomorrow:
                rows = [{**flight("OZ152", "10:30", date="2026-11-20", code="SDJ"), "destination_name": "센다이"}]
            return {"ok": True, "data": {"airport": "ICN", "airport_name": "인천", "date": args["date"],
                                         "provider": "IIAC", "count": len(rows), "flights": rows, "note": None}}
        return {"ok": True, "data": {"count": 0, "rows": [], "current_hour": 9, "selectdate": "0"}}

    monkeypatch.setattr(nodes, "call", fake)
    monkeypatch.setattr(nodes, "_today", lambda: today)
    # 지금 시각도 고정한다. 실제 시계를 쓰면 가짜 편(11-20 10:30)이 지난 편이 되어 결과가 날마다 달라진다.
    from app.agent.llm import KST
    monkeypatch.setattr(nodes, "now_kst", lambda: datetime(int(today[:4]), int(today[4:6]), int(today[6:]), 9, 0, tzinfo=KST))


def test_full_flow_replans_to_next_day_and_remembers(monkeypatch):
    fake_tools(monkeypatch, TODAY)
    trace = []
    res = graph.run_agent("빠른 시일내에 센다이에 가고 싶어", "t1", trace)
    events = [e["event"] for e in trace]
    assert res["replans"] == 1 and "replanned" in events
    assert "추천:" in res["answer"] and "OZ152" in res["answer"]
    assert "판단 기준" in res["answer"] and "근거 자료" in res["answer"] and "가정:" in res["answer"]
    assert res["cards"]["chosen"] == "OZ152"
    assert events[0] == "request_received" and "step_started" in events and events[-1] == "memory_updated"

    trace2 = []
    res2 = graph.run_agent("김포에서는?", "t1", trace2)          # 목적지는 이어받고 출발 공항만 바뀐다
    called = [e["payload"]["args"] for e in trace2 if e["event"] == "tool_called"]
    assert called[0]["airport"] == "GMP" and called[0]["destination"] == "SDJ"
    assert res2["intent"] == "departures"


def test_greeting_calls_no_tools(monkeypatch):
    fake_tools(monkeypatch, TODAY)
    trace = []
    res = graph.run_agent("안녕", "t2", trace)
    assert "답할 수 있습니다" in res["answer"]
    assert not [e for e in trace if e["event"] == "tool_called"]


def test_graph_compiles_when_langgraph_installed():
    pytest.importorskip("langgraph")
    assert graph.build_graph(noop) is not None


def test_new_chat_cancels_running_agent(monkeypatch):
    from app.agent import cancel
    from app.agent.memory import memory as mem

    fake_tools(monkeypatch, TODAY)
    real = nodes.call

    def slow(tool, **args):                    # 첫 도구를 부르는 동안 사용자가 '새 대화'를 누른 상황
        cancel.request("run-x")
        return real(tool, **args)
    monkeypatch.setattr(nodes, "call", slow)
    trace = []
    res = graph.run_agent("빠른 시일내에 센다이에 가고 싶어", "c1", trace, run_id="run-x")
    events = [e["event"] for e in trace]
    assert res["cancelled"] is True and res["answer"] is None
    assert events[-1] == "cancelled" and "responded" not in events
    assert events.count("tool_called") == 1     # 멈춤 요청 뒤에는 도구를 더 부르지 않는다
    assert mem.get("c1") is None                 # 멈춘 실행은 대화 맥락에 남기지 않는다
    assert not cancel.is_cancelled("run-x")      # 끝나면 멈춤 표시를 지운다


def test_cancel_before_start_stops_at_first_step(monkeypatch):
    from app.agent import cancel
    fake_tools(monkeypatch, TODAY)
    cancel.request("run-y")
    trace = []
    res = graph.run_agent("안녕", "c2", trace, run_id="run-y")
    assert res["cancelled"] and [e["event"] for e in trace][-1] == "cancelled"
    assert not [e for e in trace if e["event"] == "step_started"]
