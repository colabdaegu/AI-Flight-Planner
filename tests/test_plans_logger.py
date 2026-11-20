"""여행 계획 저장 조건 · 겹침 경고, 실행 로그 기록 테스트."""
import pytest

from app.agent.constraints import overlaps
from app.logger_agent import AgentLogger
from app.store.plans import PlanError, PlanStore


def plan(**kw):
    base = {"title": "출장", "memo": "회의", "airport": "gmp", "flight_no": "ke 123", "line": "domestic",
            "estimated": "2026-11-20 09:00", "buffer_min": 40, "user_arrival": "2026-11-20 08:00"}
    base.update(kw)
    return base


def test_overlap_boundary_is_not_overlap():
    assert not overlaps("2026-11-20 07:00", "2026-11-20 08:00", "2026-11-20 08:00", "2026-11-20 09:00")
    assert overlaps("2026-11-20 07:00", "2026-11-20 08:30", "2026-11-20 08:00", "2026-11-20 09:00")


def test_checkin_deadline_uses_typed_minutes(tmp_path):
    """체크인 마감 = 출발 − 입력한 공항 체크인 시간(40분)."""
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    res = store.create(plan())
    p = res["plan"]
    assert (p["airport"], p["flight_no"], p["memo"]) == ("GMP", "KE123", "회의")
    assert p["checkin_min"] == 40 and p["checkin_deadline"] == "2026-11-20 08:20" and p["arrive_by"] == "2026-11-20 08:20"
    assert p["time_source"] == "manual" and p["arrival_auto"] == 0 and res["warnings"] == []


def test_arrival_and_buffer_default_by_line(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    dom = store.create(plan(user_arrival=None, buffer_min=None))["plan"]
    assert dom["buffer_min"] == 60 and dom["user_arrival"] == "2026-11-20 08:00" and dom["arrival_auto"] == 1
    lookup = {"scheduled": "2026-11-21 10:00", "estimated": None, "destination": "NRT"}
    intl = store.create(plan(line="international", user_arrival=None, buffer_min=None), lookup)["plan"]
    # 입력이 없으면 국제선 기본 120분 → 10:00 − 120분 = 08:00
    assert intl["checkin_min"] == 120 and intl["checkin_deadline"] == "2026-11-21 08:00"
    assert intl["user_arrival"] == "2026-11-21 08:00"


def test_line_mismatch_is_rejected(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    lookup = {"scheduled": "2026-11-20 10:00", "estimated": None, "destination": "NRT"}
    with pytest.raises(PlanError) as e:
        store.create(plan(line="domestic"), lookup)
    assert e.value.code == "LINE_MISMATCH" and "국제선" in str(e.value)
    assert store.list() == []


def test_flight_lookup_wins_over_typed_time(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    lookup = {"scheduled": "2026-11-20 10:00", "estimated": "2026-11-20 10:20", "destination": "NRT"}
    p = store.create(plan(line="international", estimated="2026-11-20 06:00", buffer_min=None, user_arrival=None), lookup)["plan"]
    assert p["scheduled"] == "2026-11-20 10:00" and p["estimated"] == "2026-11-20 10:20"
    assert p["checkin_min"] == 120 and p["checkin_deadline"] == "2026-11-20 08:20" and p["time_source"] == "flight"


def test_arrival_after_checkin_deadline_is_rejected(tmp_path):
    """체크인 마감(08:20)까지 도착해야 저장된다. 여유 = 마감 − 도착."""
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    with pytest.raises(PlanError) as e:
        store.create(plan(user_arrival="2026-11-20 08:25"))
    assert e.value.code == "CONDITION_NOT_MET"
    res = store.create(plan(user_arrival="2026-11-20 08:00"))
    assert res["warnings"] == [] and res["plan"]["checkin_deadline"] == "2026-11-20 08:20"


def test_no_time_and_no_lookup_asks_for_time(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    with pytest.raises(PlanError) as e:
        store.create(plan(estimated=None))
    assert e.value.code == "MISSING"


def test_overlap_warns_but_saves(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    store.create(plan())
    res = store.create(plan(title="다른 일정", estimated="2026-11-20 09:30", user_arrival="2026-11-20 06:00"))
    assert res["warnings"] and "출장" in res["warnings"][-1]
    assert len(store.list()) == 2


def test_update_and_delete(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    pid = store.create(plan())["plan"]["id"]
    res = store.update(pid, plan(title="수정", buffer_min=0))
    assert res["plan"]["title"] == "수정" and res["plan"]["arrive_by"] == "2026-11-20 09:00"
    assert store.delete(pid) is True
    assert store.delete(pid) is False


def test_missing_field_reported(tmp_path):
    store = PlanStore(str(tmp_path / "p.sqlite3"))
    with pytest.raises(PlanError) as e:
        store.create(plan(line=""))
    assert e.value.code == "MISSING"


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.sqlite3")
    con = sqlite3.connect(path)
    from app.store.plans import SCHEMA
    con.execute(SCHEMA)
    con.commit()
    con.close()
    p = PlanStore(path).create(plan())["plan"]
    assert p["memo"] == "회의" and p["checkin_deadline"]


def test_logger_appends_and_reads(tmp_path):
    lg = AgentLogger(str(tmp_path / "db.sqlite3"))
    lg.on_event({"event": "request_received", "session_id": "abc_1", "ts": "t", "payload": {}})
    lg.on_event({"event": "responded", "session_id": "abc_1", "ts": "t", "payload": {"chars": 3}})
    events = lg.read("abc_1")
    assert [e["event"] for e in events] == ["request_received", "responded"]
    assert lg.list_sessions()[0]["session_id"] == "abc_1"
    assert lg.delete("abc_1") is True
    assert lg.read("abc_1") is None


def test_logger_rejects_unsafe_session_id(tmp_path):
    lg = AgentLogger(str(tmp_path / "db.sqlite3"))
    with pytest.raises(ValueError):
        lg.read("../secret")


def test_log_list_shows_first_question_and_counts(tmp_path):
    logger = AgentLogger(str(tmp_path / "db.sqlite3"))
    for ev, payload in [("request_received", {"message": "센다이 가는 편"}), ("tool_called", {"tool": "x"}),
                        ("tool_result", {"ok": False}), ("replanned", {"attempt": 1})]:
        logger.on_event({"event": ev, "session_id": "abc", "ts": "2026-11-19T15:00:00", "payload": payload})
    item = logger.list_sessions()[0]
    assert item["title"] == "센다이 가는 편"
    assert (item["tool_calls"], item["errors"], item["replans"], item["events"]) == (1, 1, 1, 4)


def test_logs_and_memory_survive_restart(tmp_path):
    """서버를 다시 켠 것처럼 새 객체로 열어도 실행 로그와 대화 맥락이 남는다."""
    from app.agent.memory import SessionMemory
    path = str(tmp_path / "db.sqlite3")
    AgentLogger(path).on_event({"event": "request_received", "session_id": "s1", "ts": "t", "payload": {"message": "q"}})
    SessionMemory(path).remember("s1", {"intent": "departures", "airport": "PUS"}, "요약", "q")
    assert AgentLogger(path).read("s1")[0]["payload"]["message"] == "q"
    assert SessionMemory(path).get("s1")["intent"]["airport"] == "PUS"


def test_legacy_jsonl_logs_are_imported_once(tmp_path):
    import json
    legacy = tmp_path / "logs"
    legacy.mkdir()
    (legacy / "old1.jsonl").write_text(json.dumps({"event": "request_received", "ts": "t", "payload": {"message": "예전"}},
                                                  ensure_ascii=False) + "\n", encoding="utf-8")
    path = str(tmp_path / "db.sqlite3")
    AgentLogger(path, legacy_dir=str(legacy))
    AgentLogger(path, legacy_dir=str(legacy))          # 두 번 켜도 한 번만 옮긴다
    assert len(AgentLogger(path).read("old1")) == 1
