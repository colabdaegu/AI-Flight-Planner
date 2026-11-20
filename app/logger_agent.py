"""로깅 서브 에이전트 (명세 F3012 · F3013 · F3014). 이벤트 구독자이며 LLM 을 쓰지 않는다.

- 받은 이벤트를 그대로 SQLite(events 표)에 발생 순서대로 저장한다. 서버를 다시 켜도 남는다.
- 조회와 삭제만 있다. 수정 함수·API 는 두지 않는다.
- 저장에 실패해도 대화는 계속된다 (EventBus 가 구독자 예외를 삼킨다).
- 예전 형식(data/logs/<세션>.jsonl)이 있으면 처음 켤 때 한 번 옮겨 온다.
- 심화 F3020: 실행(질문 1건)이 끝나면(memory_updated · agent_error) 그 실행을 규칙으로 판정해 audits 표에 남긴다.
  판정에는 LLM 을 쓰지 않는다. 이상으로 판정한 실행에만 따로 LLM 원인 진단을 붙인다(백그라운드).
"""
import json
import os
import re
import threading

from app import audit, config, db
from app.events import bus
from app.store.audits import AuditStore, audit_store

SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCREEN_SESSION = "screen"     # 화면에서 직접 부른 조회 기록을 모으는 세션
SCHEMA = """CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL
)"""
INDEX = "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id)"


RUN_END = ("memory_updated", "agent_error", "cancelled")      # 실행 한 건이 끝났음을 알리는 이벤트


class AgentLogger:
    def __init__(self, db_path=None, legacy_dir=None, audits=None, diagnose_async=True):
        self.db_path = db_path
        self.legacy_dir = legacy_dir if legacy_dir is not None else (None if db_path else config.LOG_DIR)
        self.audits = audits or (AuditStore(db_path) if db_path else audit_store)
        self.diagnose_async = diagnose_async   # 진단(LLM)은 응답을 늦추지 않게 따로 돈다. 테스트는 False
        self._run(lambda con: (con.execute(SCHEMA), con.execute(INDEX)))
        self._import_legacy()

    def _run(self, fn):
        return db.run(fn, self.db_path)

    @staticmethod
    def check(sid):
        if not SAFE.match(sid or ""):
            raise ValueError("잘못된 세션 번호")
        return sid

    _path = check          # 예전 이름 (세션 번호 형식 확인용)

    def _import_legacy(self):
        folder = self.legacy_dir
        if not folder or not os.path.isdir(folder):
            return
        files = [n for n in os.listdir(folder) if n.endswith(".jsonl") and SAFE.match(n[:-6])]
        if not files:
            return

        def op(con):
            for name in files:
                sid = name[:-6]
                if con.execute("SELECT 1 FROM events WHERE session_id = ? LIMIT 1", (sid,)).fetchone():
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    for line in f:
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        con.execute("INSERT INTO events (session_id, ts, event, payload) VALUES (?, ?, ?, ?)",
                                    (sid, e.get("ts", ""), e.get("event", ""),
                                     json.dumps(e.get("payload") or {}, ensure_ascii=False)))
        try:
            self._run(op)
        except Exception as e:                      # 옮기기 실패는 서비스에 영향을 주지 않는다
            print("[log-import]", type(e).__name__)

    def on_event(self, msg):
        sid = self.check(msg["session_id"])
        self._run(lambda con: con.execute(
            "INSERT INTO events (session_id, ts, event, payload) VALUES (?, ?, ?, ?)",
            (sid, msg["ts"], msg["event"], json.dumps(msg.get("payload") or {}, ensure_ascii=False))))
        if msg["event"] in RUN_END and sid != SCREEN_SESSION:
            try:
                self.audit_last(sid)
            except Exception as e:                 # 판정 실패도 대화에는 영향을 주지 않는다
                print("[audit]", type(e).__name__)

    # ---------------- 이상 징후 판단 (F3020) ----------------
    def audit_last(self, sid):
        """방금 끝난 실행 하나를 판정해 저장한다."""
        runs = audit.split_runs(self.read(sid) or [])
        if not runs:
            return None
        return self._judge_and_store(sid, runs[-1], len(runs) - 1)

    def audit(self, sid):
        """세션의 모든 실행을 판정해 돌려준다 (이 기능 전에 쌓인 로그도 판정). 진단이 없는 이상 실행은 진단을 건다."""
        self.check(sid)
        if sid == SCREEN_SESSION:
            return []
        runs = audit.split_runs(self.read(sid) or [])
        return [self._judge_and_store(sid, run, i) for i, run in enumerate(runs)]

    def _judge_and_store(self, sid, run, index):
        verdict = audit.judge_run(run, index)
        saved = self.audits.save(sid, verdict)
        saved["undetermined"] = verdict["undetermined"]
        if verdict["status"] == "anomaly" and not saved.get("diagnosis"):
            pending = {"status": "pending", "reason": "원인 진단 중"}
            self.audits.set_diagnosis(sid, index, pending)
            saved["diagnosis"] = pending
            if self.diagnose_async:
                threading.Thread(target=self._diagnose, args=(sid, run, verdict), daemon=True).start()
            else:
                self._diagnose(sid, run, verdict)
                saved["diagnosis"] = self.audits.get(sid, index)["diagnosis"]
        return saved

    def _diagnose(self, sid, run, verdict):
        result = audit.diagnose(run, verdict)
        if result is not None:
            self.audits.set_diagnosis(sid, verdict["run_index"], result)

    def list_sessions(self):
        def op(con):
            return con.execute("SELECT session_id, MIN(ts) AS started, MAX(ts) AS updated, COUNT(*) AS n "
                               "FROM events GROUP BY session_id ORDER BY MAX(id) DESC").fetchall()
        items = []
        anomalies = self.audits.anomaly_counts()
        for r in self._run(op):
            sid = r["session_id"]
            items.append({**self._summary(sid), "session_id": sid, "started": r["started"],
                          "updated": r["updated"], "events": r["n"], "anomalies": anomalies.get(sid, 0)})
        return items

    def _summary(self, sid):
        """목록용 요약: 첫 질문, 도구 호출·재계획·실패 수. 읽기만 한다."""
        title, tools, replans, errors = None, 0, 0, 0
        for e in self.read(sid) or []:
            p = e["payload"]
            kind = e["event"]
            if kind == "request_received" and title is None:
                title = p.get("message") or f"질문 {p.get('chars', '?')}자"
            elif kind in ("tool_called", "screen_query"):
                tools += 1
                if kind == "screen_query" and p.get("ok") is False:
                    errors += 1
            elif kind == "replanned":
                replans += 1
            elif kind == "tool_result" and p.get("ok") is False:
                errors += 1
        if sid == SCREEN_SESSION:
            title = "화면에서 직접 조회한 기록"
        return {"title": title or "(질문 없음)", "kind": "screen" if sid == SCREEN_SESSION else "chat",
                "tool_calls": tools, "replans": replans, "errors": errors}

    def read(self, sid):
        self.check(sid)
        rows = self._run(lambda con: con.execute(
            "SELECT session_id, ts, event, payload FROM events WHERE session_id = ? ORDER BY id", (sid,)).fetchall())
        if not rows:
            return None
        return [{"event": r["event"], "session_id": r["session_id"], "ts": r["ts"],
                 "payload": json.loads(r["payload"])} for r in rows]

    def delete(self, sid):
        self.check(sid)
        gone = self._run(lambda con: con.execute("DELETE FROM events WHERE session_id = ?", (sid,)).rowcount) > 0
        self.audits.delete(sid)                  # 실행 로그를 지우면 그 판정·진단도 함께 지운다
        return gone


_attached = None


def attach_logger(logger=None):
    """이벤트 버스에 로거를 한 번만 연결한다."""
    global _attached
    if _attached is None:
        _attached = logger or AgentLogger()
        bus.subscribe(_attached.on_event)
    return _attached
