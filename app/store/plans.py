"""출국 계획 저장 (명세 F3015 · F3016). SQLite(data/planner.sqlite3) — 서버를 다시 켜도 남는다.

계산식
- 출발 = 편명 조회로 받은 시각(예상 출발 우선). 사용자가 직접 고친 항공기 출발 시간(dep_manual)이 있으면 그 값을 쓴다.
- 노선(국내선/국제선)은 사용자가 고른다. 편명 조회로 목적지를 알면 맞는지 확인하고, 다르면 저장하지 않는다.
- 체크인 마감 = 출발 − 공항·노선별 마감 분 (app.checkin: 국내선 30분, 국제선 60분)
- 준비 시간(공항에 출발 몇 분 전 도착할지) = 입력값, 없으면 국내선 60분 · 국제선 120분
- 권장 도착 = 출발 − 준비 시간 (체크인 마감보다 늦을 수 없다)
- 내 공항 도착 예정 = 입력값, 없으면 권장 도착
- 조건: 내 도착 ≤ 체크인 마감. 아니면 저장하지 않는다 (CONDITION_NOT_MET).
  권장 도착보다 늦지만 마감 전이면 저장하고 경고한다. 같은 날 준비 구간이 겹쳐도 경고한다.
"""
from datetime import datetime, timedelta

from app import airports, checkin, config, db
from app.tools.common import flight_no_key
from app.agent import constraints
from app.agent.constraints import overlaps

SCHEMA = """CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    airport TEXT NOT NULL,
    flight_no TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    scheduled TEXT NOT NULL,
    estimated TEXT,
    buffer_min INTEGER NOT NULL,
    user_arrival TEXT NOT NULL,
    arrive_by TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""
# 나중에 더한 열 (예전 파일도 그대로 열리게 ALTER 로 붙인다)
EXTRA = {"memo": "TEXT", "destination": "TEXT", "line": "TEXT", "checkin_deadline": "TEXT",
         "checkin_min": "INTEGER", "time_source": "TEXT", "arrival_auto": "INTEGER"}
COLS = ["title", "memo", "airport", "flight_no", "flight_date", "destination", "line", "scheduled", "estimated",
        "checkin_min", "checkin_deadline", "buffer_min", "user_arrival", "arrive_by", "time_source", "arrival_auto"]
FMT = constraints.FMT


class PlanError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _time(value, name):
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value).replace("T", " ")[:16], FMT).strftime(FMT)
    except ValueError:
        raise PlanError("BAD_VALUE", f"{name} 형식은 YYYY-MM-DD HH:MM 입니다.")


LINE_NAME = {"domestic": "국내선", "international": "국제선"}


def deadline_of(departure, buffer_min):
    """체크인 마감 = 출발 − 공항 체크인 시간(분)."""
    return (datetime.strptime(departure, FMT) - timedelta(minutes=int(buffer_min))).strftime(FMT)


def _fresh(row):
    """예전 방식(항공사 고정 30·60분)으로 저장된 계획도 '출발 − 체크인 시간'으로 다시 계산해 보여 준다."""
    if not row:
        return row
    row = dict(row)
    dep = row.get("estimated") or row.get("scheduled")
    if dep and row.get("buffer_min") is not None:
        row["checkin_deadline"] = deadline_of(dep, row["buffer_min"])
        row["checkin_min"] = row["buffer_min"]
        row["arrive_by"] = row["checkin_deadline"]
    return row
DEFAULT_BUFFER = {"domestic": 60, "international": 120}


def build(data, lookup=None):
    """입력과 편명 조회 결과로 저장할 한 줄과 경고를 만든다. lookup: {scheduled, estimated, destination} 또는 None."""
    missing = [label for key, label in (("title", "제목"), ("airport", "공항"), ("flight_no", "편명"),
                                        ("line", "국내선/국제선")) if data.get(key) in (None, "")]
    if missing:
        raise PlanError("MISSING", f"필수 항목 누락: {', '.join(missing)}")
    airport = str(data["airport"]).strip().upper()
    if airport not in airports.NAMES:
        raise PlanError("BAD_VALUE", f"지원하지 않는 공항: {airport}")
    line = data["line"]
    if line not in LINE_NAME:
        raise PlanError("BAD_VALUE", "노선은 국내선 또는 국제선입니다.")
    if len(str(data["flight_no"]).replace(" ", "")) > 6:
        raise PlanError("BAD_VALUE", "편명은 6자 이내입니다 (예: KE1041).")
    flight_no = flight_no_key(data["flight_no"])
    user_dep = _time(data.get("estimated") or data.get("scheduled"), "항공기 출발 시간")
    arrival = _time(data.get("user_arrival"), "내 공항 도착 예정")
    if lookup and lookup.get("scheduled"):
        destination = lookup.get("destination")
        if destination:
            actual = checkin.line_of(airport, destination)
            if actual != line:
                raise PlanError("LINE_MISMATCH",
                                f"{flight_no} 편은 {LINE_NAME[actual]}입니다 ({airport}→{destination}). "
                                f"{LINE_NAME[actual]}으로 고치십시오.")
        if data.get("dep_manual") and user_dep:
            scheduled, estimated, source = user_dep, None, "manual"
        else:
            scheduled, estimated, source = lookup["scheduled"], lookup.get("estimated"), "flight"
    elif user_dep:
        scheduled, estimated, destination, source = user_dep, None, None, "manual"
    else:
        raise PlanError("MISSING", "도착 뒤 24시간 안에 출발 편을 찾지 못했습니다. 항공기 출발 시간을 직접 넣어 주십시오.")
    try:
        raw_buffer = data.get("buffer_min")
        buffer_min = DEFAULT_BUFFER[line] if raw_buffer in (None, "") else int(raw_buffer)
    except (TypeError, ValueError):
        raise PlanError("BAD_VALUE", "준비 시간은 분 단위 숫자입니다.")
    if not 0 <= buffer_min <= 600:
        raise PlanError("BAD_VALUE", "준비 시간은 0~600분입니다.")
    departure = estimated or scheduled
    # 체크인 마감 = 항공기 출발 시간 − 공항 체크인 시간(분). 사용자가 넣은 값을 쓰고, 비우면 국내선 60 · 국제선 120분.
    # (공항·항공사마다 달라 고정값으로 정하지 않는다.)
    deadline = deadline_of(departure, buffer_min)
    arrive_by = deadline
    auto = arrival is None
    arrival = arrival or arrive_by
    warns = []
    if datetime.strptime(arrival, FMT) > datetime.strptime(deadline, FMT):
        raise PlanError("CONDITION_NOT_MET", f"도착 예정 {arrival[11:]}이 체크인 마감 {deadline[11:]}"
                                             f"(출발 {departure[11:]} − 체크인 {buffer_min}분)보다 늦습니다.")
    row = {"title": str(data["title"]).strip(), "memo": (str(data.get("memo") or "").strip() or None),
           "airport": airport, "flight_no": flight_no,
           "flight_date": departure[:10], "destination": destination, "line": line,
           "scheduled": scheduled, "estimated": estimated if estimated and estimated != scheduled else None,
           "checkin_min": buffer_min, "checkin_deadline": deadline,
           "buffer_min": buffer_min, "user_arrival": arrival, "arrive_by": arrive_by, "time_source": source,
           "arrival_auto": 1 if auto else 0}
    return row, warns


class PlanStore:
    def __init__(self, path=None):
        self.path = path or config.PLAN_DB

        def init(con):
            con.execute(SCHEMA)
            have = {r["name"] for r in con.execute("PRAGMA table_info(plans)").fetchall()}
            for col, typ in EXTRA.items():
                if col not in have:
                    con.execute(f"ALTER TABLE plans ADD COLUMN {col} {typ}")
        self._run(init)

    def _run(self, fn):
        return db.run(fn, self.path)

    @staticmethod
    def _warnings(con, row, exclude_id=None):
        rows = con.execute("SELECT * FROM plans WHERE flight_date = ?", (row["flight_date"],)).fetchall()
        warns = []
        for r in rows:
            if r["id"] == exclude_id:
                continue
            r = _fresh(r)
            dep_r = r["estimated"] or r["scheduled"]
            dep_new = row["estimated"] or row["scheduled"]
            if overlaps(row["arrive_by"], dep_new, r["arrive_by"], dep_r):
                warns.append(f"'{r['title']}'(#{r['id']})과 공항 준비 시간이 겹칩니다.")
        return warns

    def create(self, data, lookup=None):
        row, warns = build(data, lookup)
        row["created_at"] = datetime.now().isoformat(timespec="seconds")

        def op(con):
            warns.extend(self._warnings(con, row))
            cols = COLS + ["created_at"]
            sql = f"INSERT INTO plans ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
            cur = con.execute(sql, [row[c] for c in cols])
            return cur.lastrowid, warns

        pid, warns = self._run(op)
        return {"plan": self.get(pid), "warnings": warns}

    def list(self):
        rows = self._run(lambda con: con.execute(
            "SELECT * FROM plans ORDER BY id").fetchall())
        return [_fresh(r) for r in rows]

    def get(self, pid):
        r = self._run(lambda con: con.execute("SELECT * FROM plans WHERE id = ?", (pid,)).fetchone())
        return _fresh(r) if r else None

    def update(self, pid, data, lookup=None):
        if self.get(pid) is None:
            return None
        row, warns = build(data, lookup)

        def op(con):
            warns.extend(self._warnings(con, row, exclude_id=pid))
            sets = ", ".join(f"{c} = ?" for c in COLS)
            con.execute(f"UPDATE plans SET {sets} WHERE id = ?", [row[c] for c in COLS] + [pid])
            return warns

        warns = self._run(op)
        return {"plan": self.get(pid), "warnings": warns}

    def delete(self, pid):
        return self._run(lambda con: con.execute("DELETE FROM plans WHERE id = ?", (pid,)).rowcount) > 0
