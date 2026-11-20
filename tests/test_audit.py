"""심화 F3020: 로깅 에이전트의 이상 징후 판단."""
from app import audit
from app.agent import graph
from app.logger_agent import AgentLogger
from app.store.audits import AuditStore

from tests.test_agent import TODAY, fake_tools


def E(event, **payload):
    return {"event": event, "session_id": "s", "ts": "2026-11-20T10:00:00", "payload": payload}


DEP = {"airport": "GMP", "date": "20261120", "destination": "CJU"}


def run(*middle, finished=True):
    events = [E("request_received", message="김포에서 제주"), *middle]
    if finished:
        events.append(E("responded", chars=10))
    return events


def planned(*steps, followups=()):
    return E("planned", steps=[{"tool": t, "args": a} for t, a in steps], followups=list(followups))


def call(tool, args, ok=True):
    return [E("tool_called", tool=tool, args=args), E("tool_result", tool=tool, ok=ok)]


def test_normal_run_has_no_warning():
    v = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP)))
    assert v["status"] == "normal" and v["warnings"] == []


def test_same_call_after_success_is_repeated_call():
    v = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP),
                            planned(("search_departures", DEP)), *call("search_departures", DEP)))
    assert v["status"] == "anomaly"
    assert [w["code"] for w in v["warnings"]] == ["REPEATED_CALL"]
    assert len(v["warnings"][0]["evidence"]) == 2


def test_one_retry_after_failure_is_allowed_but_two_are_not():
    once = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP, ok=False),
                               planned(("search_departures", DEP)), *call("search_departures", DEP)))
    assert once["status"] == "normal"
    twice = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP, ok=False),
                                *call("search_departures", DEP, ok=False), *call("search_departures", DEP)))
    assert [w["code"] for w in twice["warnings"]] == ["REPEATED_CALL"]


def test_replans_reaching_limit_are_excessive():
    events = run(planned(("search_departures", DEP)), *call("search_departures", DEP),
                 *[E("replanned", attempt=n) for n in (1, 2, 3)])
    v = audit.judge_run(events, limit=3)
    assert [w["code"] for w in v["warnings"]] == ["EXCESSIVE_REPLAN"]
    assert audit.judge_run(events, limit=4)["status"] == "normal"


def test_call_not_in_plan_is_flagged_but_declared_followup_is_not():
    other = {"airport": "GMP", "flight_no": "KE1041", "date": "20261120", "direction": "departure"}
    v = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP),
                            *call("get_flight_detail", other)))
    assert [w["code"] for w in v["warnings"]] == ["UNPLANNED_CALL"]
    ok = audit.judge_run(run(planned(("search_departures", DEP), followups=["get_flight_detail"]),
                             *call("search_departures", DEP), *call("get_flight_detail", other)))
    assert ok["status"] == "normal"


def test_missing_evidence_is_undetermined_not_normal():
    no_plan = audit.judge_run(run(*call("search_departures", DEP)))
    assert no_plan["status"] == "undetermined" and no_plan["warnings"] == []
    unfinished = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP),
                                     finished=False))
    assert unfinished["status"] == "undetermined"
    headless = audit.judge_run([E("planned", steps=[]), E("responded")])
    assert headless["status"] == "undetermined"


def test_runs_are_split_by_request():
    events = run(planned()) + run(planned(("search_departures", DEP)), *call("search_departures", DEP))
    verdicts = audit.judge_session(events)
    assert [v["run_index"] for v in verdicts] == [0, 1]
    assert verdicts[0]["question"] == "김포에서 제주"


def test_diagnosis_only_for_anomaly_and_kept_apart():
    calls = []

    def fake_llm(system, user):
        calls.append(user)
        return "메인 Agent 실행 단계 문제입니다. 근거는 4번과 6번 이벤트입니다. 성공한 결과를 캐시에서 재사용하십시오. 넷째 문장."

    normal = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP)))
    assert audit.diagnose([], normal, llm=fake_llm) is None and calls == []
    bad = audit.judge_run(run(planned(("search_departures", DEP)), *call("search_departures", DEP),
                              *call("search_departures", DEP)))
    d = audit.diagnose([], bad, llm=fake_llm)
    assert d["status"] == "done" and "넷째" not in d["text"] and d["text"].endswith("재사용하십시오.") and len(calls) == 1
    assert len(bad["warnings"]) == 1                     # 진단은 경고 수에 섞이지 않는다

    def broken(system, user):
        raise TimeoutError("x")
    assert audit.diagnose([], bad, llm=broken)["status"] == "failed"


def test_logger_judges_each_run_and_main_agent_reads_previous(monkeypatch, tmp_path):
    from app.events import bus
    db = str(tmp_path / "audit.sqlite3")
    store = AuditStore(db)
    logger = AgentLogger(db_path=db, audits=store, diagnose_async=False)
    monkeypatch.setattr(bus, "_subs", [logger.on_event])
    monkeypatch.setattr(graph, "audit_store", store)
    fake_tools(monkeypatch, TODAY)

    graph.run_agent("빠른 시일내에 센다이에 가고 싶어", "a1", [])
    first = store.latest("a1")
    assert first["run_index"] == 0 and first["status"] == "normal"
    assert first["diagnosis"] is None                    # 정상 실행에는 진단(LLM)을 붙이지 않는다

    trace = []
    graph.run_agent("김포에서는?", "a1", trace)
    ref = [e for e in trace if e["event"] == "audit_referenced"]
    assert ref and ref[0]["payload"]["status"] == "normal" and ref[0]["payload"]["run_index"] == 0
    assert [a["run_index"] for a in store.list("a1")] == [0, 1]

    logger.delete("a1")
    assert store.list("a1") == []                        # 로그를 지우면 판정도 지운다


def test_anomaly_gets_diagnosis_without_key_as_skipped(tmp_path):
    db = str(tmp_path / "audit2.sqlite3")
    store = AuditStore(db)
    logger = AgentLogger(db_path=db, audits=store, diagnose_async=False)
    events = run(planned(("search_departures", DEP)), *call("search_departures", DEP),
                 *call("search_departures", DEP)) + [E("memory_updated")]
    for e in events:
        logger.on_event(e)
    a = store.latest("s")
    assert a["status"] == "anomaly" and a["warnings"][0]["code"] == "REPEATED_CALL"
    assert a["diagnosis"]["status"] == "skipped"         # 테스트는 GMS 키가 없으므로 진단을 건너뛴다
    assert logger.list_sessions()[0]["anomalies"] == 1
