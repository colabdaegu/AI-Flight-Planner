"""이상 징후 판정 결과 저장소 (심화 F3020).

- 로깅 에이전트가 실행(질문 1건) 단위로 판정을 쓰고, 메인 에이전트와 API 가 읽는다.
- 실행 로그(events 표)와 다른 표에 둔다. 실행 로그는 고치지 않고, 판정과 진단은 따로 남긴다.
- 판정(warnings·checks)과 LLM 원인 진단(diagnosis)도 서로 다른 칸에 둔다. 진단은 경고 수에 섞지 않는다.
"""
import json
from datetime import datetime

from app import db

SCHEMA = """CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    question TEXT,
    started TEXT,
    judged_at TEXT NOT NULL,
    status TEXT NOT NULL,
    warnings TEXT NOT NULL,
    checks TEXT NOT NULL,
    diagnosis TEXT,
    UNIQUE(session_id, run_index)
)"""

STATUS_LABEL = {"normal": "이상 없음", "anomaly": "이상", "undetermined": "판정 불가"}


def _row(r):
    return {"session_id": r["session_id"], "run_index": r["run_index"], "question": r["question"],
            "started": r["started"], "judged_at": r["judged_at"], "status": r["status"],
            "status_label": STATUS_LABEL.get(r["status"], r["status"]),
            "warnings": json.loads(r["warnings"]), "checks": json.loads(r["checks"]),
            "diagnosis": json.loads(r["diagnosis"]) if r["diagnosis"] else None}


class AuditStore:
    def __init__(self, path=None):
        self.path = path

    def _run(self, fn):
        def op(con):
            con.execute(SCHEMA)          # 테스트처럼 DB 경로가 바뀌어도 표가 있게 매번 확인한다
            return fn(con)
        return db.run(op, self.path)

    def save(self, session_id, verdict):
        """판정을 저장한다. 같은 실행을 다시 판정하면 판정만 바꾸고, 이미 있는 진단은 그대로 둔다."""
        now = datetime.now().isoformat(timespec="seconds")

        def op(con):
            con.execute(
                "INSERT INTO audits (session_id, run_index, question, started, judged_at, status, warnings, checks) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, run_index) DO UPDATE SET question=excluded.question, "
                "started=excluded.started, judged_at=excluded.judged_at, status=excluded.status, "
                "warnings=excluded.warnings, checks=excluded.checks",
                (session_id, verdict["run_index"], verdict.get("question"), verdict.get("started"), now,
                 verdict["status"], json.dumps(verdict["warnings"], ensure_ascii=False),
                 json.dumps(verdict["checks"], ensure_ascii=False)))
        self._run(op)
        return self.get(session_id, verdict["run_index"])

    def set_diagnosis(self, session_id, run_index, diagnosis):
        self._run(lambda con: con.execute(
            "UPDATE audits SET diagnosis = ? WHERE session_id = ? AND run_index = ?",
            (json.dumps(diagnosis, ensure_ascii=False), session_id, run_index)))

    def get(self, session_id, run_index):
        r = self._run(lambda con: con.execute(
            "SELECT * FROM audits WHERE session_id = ? AND run_index = ?", (session_id, run_index)).fetchone())
        return _row(r) if r else None

    def list(self, session_id):
        rows = self._run(lambda con: con.execute(
            "SELECT * FROM audits WHERE session_id = ? ORDER BY run_index", (session_id,)).fetchall())
        return [_row(r) for r in rows]

    def latest(self, session_id):
        """메인 에이전트가 참조하는 형식: 이 세션의 가장 최근 실행 판정 (없으면 None)."""
        r = self._run(lambda con: con.execute(
            "SELECT * FROM audits WHERE session_id = ? ORDER BY run_index DESC LIMIT 1", (session_id,)).fetchone())
        return _row(r) if r else None

    def anomaly_counts(self):
        rows = self._run(lambda con: con.execute(
            "SELECT session_id, COUNT(*) AS n FROM audits WHERE status = 'anomaly' GROUP BY session_id").fetchall())
        return {r["session_id"]: r["n"] for r in rows}

    def delete(self, session_id):
        self._run(lambda con: con.execute("DELETE FROM audits WHERE session_id = ?", (session_id,)))


audit_store = AuditStore()
