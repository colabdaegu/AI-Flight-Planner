"""SQLite 연결 공통부. 계획·실행 로그·대화 맥락이 같은 파일(data/planner.sqlite3)을 쓴다.
서버를 껐다 켜도 남는다. 경로는 호출할 때마다 config 에서 읽어 테스트가 바꿀 수 있게 한다."""
import os
import sqlite3
import threading

from app import config

_lock = threading.Lock()


def connect(path=None):
    path = path or config.PLAN_DB
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def run(fn, path=None):
    """연결을 열어 fn(con) 을 실행하고 커밋한 뒤 닫는다. 쓰기는 한 번에 하나씩."""
    with _lock:
        con = connect(path)
        try:
            out = fn(con)
            con.commit()
            return out
        finally:
            con.close()
