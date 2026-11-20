"""대화 맥락 (명세 F3010). 세션마다 직전 조건과 결과 요약만 SQLite 에 보관한다 (서버를 다시 켜도 이어짐).

- 보관: 직전 의도·조건(공항, 목적지, 날짜 …), 추천한 편명, 최근 대화 요약 6개
- 보관하지 않음: 도구의 원본 응답 (요약만 남긴다)
- '새 대화' 로 지운다.
"""
import json
from datetime import datetime

from app import db

MAX_TURNS = 6
MAX_SESSIONS = 500
SCHEMA = """CREATE TABLE IF NOT EXISTS chat_memory (
    session_id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    summary TEXT NOT NULL,
    turns TEXT NOT NULL,
    updated TEXT NOT NULL
)"""


class SessionMemory:
    def __init__(self, db_path=None):
        self.db_path = db_path
        self._ready = None

    def _run(self, fn):
        def op(con):
            con.execute(SCHEMA)
            return fn(con)
        return db.run(op, self.db_path)

    def get(self, sid):
        r = self._run(lambda con: con.execute(
            "SELECT intent, summary, turns FROM chat_memory WHERE session_id = ?", (sid,)).fetchone())
        if r is None:
            return None
        return {"intent": json.loads(r["intent"]), "summary": r["summary"], "turns": json.loads(r["turns"])}

    def remember(self, sid, intent, summary, user_text):
        prev = self.get(sid)
        turns = ((prev or {}).get("turns") or []) + [{"user": (user_text or "")[:200], "summary": (summary or "")[:300]}]
        now = datetime.now().isoformat(timespec="seconds")

        def op(con):
            con.execute("INSERT OR REPLACE INTO chat_memory (session_id, intent, summary, turns, updated) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (sid, json.dumps(intent or {}, ensure_ascii=False), summary or "",
                         json.dumps(turns[-MAX_TURNS:], ensure_ascii=False), now))
            con.execute("DELETE FROM chat_memory WHERE session_id NOT IN "
                        "(SELECT session_id FROM chat_memory ORDER BY updated DESC LIMIT ?)", (MAX_SESSIONS,))
        self._run(op)

    def forget(self, sid):
        return self._run(lambda con: con.execute(
            "DELETE FROM chat_memory WHERE session_id = ?", (sid,)).rowcount) > 0


memory = SessionMemory()
