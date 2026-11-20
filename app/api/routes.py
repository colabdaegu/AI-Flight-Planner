"""HTTP 라우트 (명세 F3017). 모든 응답은 {ok, data} 또는 {ok:false, error:{code,message}} 봉투다.

- 대화: POST /api/chat, DELETE /api/chat/{sid} (대화 맥락 비우기)
- 계획: /api/plans (목록·상세·저장·수정·삭제)
- 실행 로그: /api/logs (목록·상세·삭제만. 수정 API 는 두지 않는다 — F3014)
- 이상 징후 판정: GET /api/logs/{sid}/audit (로깅 에이전트의 규칙 판정 + 이상일 때만 LLM 진단 — 심화 F3020)
- 화면 조회: /api/departures, /api/arrivals, /api/flight-search, /api/airport-info/{code}, /api/facilities …
  화면 조회도 'screen' 세션으로 실행 로그에 남는다.
"""
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import airports, checkin
from app.agent import cancel
from app.agent.graph import run_agent
from app.agent.memory import memory
from app.events import bus
from app.logger_agent import SCREEN_SESSION, attach_logger
from app.store.plans import PlanError, PlanStore
from app.tools import registry
from app.tools.common import fail, ok

router = APIRouter()
store = PlanStore()
logs = attach_logger()      # 이벤트를 저장하는 로깅 에이전트와 같은 객체를 조회에도 쓴다


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: Optional[str] = Field(default=None, max_length=64)
    run_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")   # 멈춤 요청에 쓰는 실행 번호


class CancelIn(BaseModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class PlanIn(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    memo: Optional[str] = Field(default=None, max_length=200)
    airport: str = Field(min_length=3, max_length=3)
    flight_no: str = Field(min_length=2, max_length=10)
    line: str = Field(pattern="^(domestic|international)$")   # 국내선 / 국제선 (편명 조회 결과와 맞아야 저장)
    estimated: Optional[str] = None          # 항공기 출발 시간 (편명 조회 결과, 또는 사용자가 직접 고친 값)
    dep_manual: bool = False                 # True면 직접 고친 출발 시간을 조회 결과보다 우선한다
    buffer_min: Optional[int] = Field(default=None, ge=0, le=600)   # 비우면 국내선 60 · 국제선 120
    user_arrival: Optional[str] = None       # 비우면 권장 도착 시각으로 채운다


def _status(res):
    if res["ok"]:
        return 200
    return 400 if res["error"]["code"] in ("BAD_ARGS",) else 502


def _error(code, message, status):
    return JSONResponse(fail(code, message), status_code=status)


def _screen(tool, **args):
    """화면에서 바로 부르는 도구. 결과를 그대로 돌려주고 'screen' 세션 실행 로그에 남긴다."""
    args = {k: v for k, v in args.items() if v not in (None, "")}
    t0 = time.perf_counter()
    res = registry.call(tool, **args)
    data = res.get("data") if res["ok"] else None
    bus.publish("screen_query", SCREEN_SESSION, tool=tool, args=args, ok=res["ok"],
                code=None if res["ok"] else res["error"]["code"],
                count=data.get("count") if isinstance(data, dict) else None,
                elapsed_ms=int((time.perf_counter() - t0) * 1000))
    return JSONResponse(res, status_code=_status(res))


# ---------------- 대화 ----------------
@router.post("/chat")
def chat(body: ChatIn):
    sid = body.session_id or uuid.uuid4().hex
    try:
        logs.check(sid)                          # 세션 번호 형식 확인 (경로 조작 방지)
    except ValueError:
        return _error("BAD_ARGS", "세션 번호 형식이 올바르지 않습니다.", 400)
    if sid == SCREEN_SESSION:
        return _error("BAD_ARGS", "사용할 수 없는 세션 번호입니다.", 400)
    trace = []
    try:
        res = run_agent(body.message, sid, trace, run_id=body.run_id)
    except Exception as e:
        print("[chat-error]", type(e).__name__)
        bus.publish("agent_error", sid, error=type(e).__name__)
        return _error("INTERNAL", "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주십시오.", 500)
    return ok({"session_id": sid, **res, "trace": trace, "audit": _latest_audit(sid)})


def _latest_audit(sid):
    """방금 끝난 실행의 판정 (로깅 에이전트가 붙어 있지 않으면 None)."""
    try:
        a = logs.audits.latest(sid)
    except Exception:
        return None
    if not a:
        return None
    return {k: a[k] for k in ("run_index", "status", "status_label", "warnings", "diagnosis")}


@router.post("/chat/cancel")
def cancel_chat(body: CancelIn):
    """'새 대화'를 누르면 진행 중인 실행을 멈춘다. 다음 단계나 다음 도구 호출 전에 멈춘다."""
    cancel.request(body.run_id)
    return ok({"run_id": body.run_id, "cancel_requested": True})


@router.delete("/chat/{sid}")
def reset_chat(sid: str):
    """새 대화: 서버에 보관한 대화 맥락만 비운다 (실행 로그는 그대로 둔다)."""
    try:
        logs.check(sid)
    except ValueError:
        return _error("BAD_ARGS", "세션 번호 형식이 올바르지 않습니다.", 400)
    return ok({"session_id": sid, "forgotten": memory.forget(sid)})


@router.get("/tools")
def tools():
    return ok(registry.catalog())


@router.get("/airports")
def airport_search(q: str = Query("", max_length=40),
                   scope: str = Query("all", pattern="^(all|origin)$")):
    """공항 자동 완성. 한글·영문 이름, 동의어, 3글자 코드 모두 검색된다."""
    return ok(airports.search(q, scope=scope))


# ---------------- 운항 ----------------
@router.get("/departures")
def departures(airport: str, date: Optional[str] = None, from_time: Optional[str] = None,
               to_time: Optional[str] = None, destination: Optional[str] = None):
    return _screen("search_departures", airport=airport, date=date, from_time=from_time,
                   to_time=to_time, destination=destination)


@router.get("/arrivals")
def arrivals(airport: str, date: Optional[str] = None, from_time: Optional[str] = None,
             to_time: Optional[str] = None, origin: Optional[str] = None):
    return _screen("search_arrivals", airport=airport, date=date, from_time=from_time,
                   to_time=to_time, origin=origin)


@router.get("/flight-search")
def flight_search(flight_no: str = Query("", max_length=20), airport: Optional[str] = Query(None, max_length=3),
                  date: Optional[str] = None):
    return _screen("search_flight", flight_no=flight_no, airport=airport, date=date)


@router.get("/flights/{airport}/{flight_no}")
def flight_detail(airport: str, flight_no: str, date: Optional[str] = None,
                  direction: str = Query("departure", pattern="^(departure|arrival)$")):
    return _screen("get_flight_detail", airport=airport, flight_no=flight_no, date=date, direction=direction)


# ---------------- 공항 시설·혼잡 ----------------
@router.get("/airport-info/{airport}")
def airport_info(airport: str):
    return _screen("get_airport_overview", airport=airport)


@router.get("/facilities")
def facilities(keyword: Optional[str] = Query(None, max_length=30),
               area: Optional[str] = Query(None, pattern="^(D|A)$"), category: Optional[str] = None):
    return _screen("get_incheon_facilities", keyword=keyword, area=area, category=category)


@router.get("/congestion/{airport}")
def congestion(airport: str):
    return _screen("get_airport_congestion", airport=airport)


@router.get("/passenger")
def passenger(selectdate: str = "0"):
    return _screen("get_incheon_passenger_forecast", selectdate=selectdate)


@router.get("/commercial/{airport}")
def commercial(airport: str, keyword: Optional[str] = Query(None, max_length=30), include_business: bool = False):
    return _screen("get_commercial_facilities", airport=airport, keyword=keyword, include_business=include_business)


# ---------------- 계획 (F3015 · F3016) ----------------
def _plan_error(e):
    status = 422 if e.code in ("CONDITION_NOT_MET", "LINE_MISMATCH") else 400
    return _error(e.code, str(e), status)


def _ymd(*values):
    for v in values:
        digits = "".join(ch for ch in str(v or "") if ch.isdigit())
        if len(digits) >= 8:
            return digits[:8]
    return None


def flight_lookup(airport, flight_no, ymd):
    """편명으로 그날 출발 시각·목적지를 찾는다. 못 찾으면 None (사용자가 넣은 시각을 쓴다)."""
    res = registry.call("get_flight_detail", airport=airport, flight_no=flight_no, date=ymd, direction="departure")
    if not res["ok"] or not res["data"]["rows"]:
        return None, (res["error"]["message"] if not res["ok"] else res["data"]["note"])
    r = res["data"]["rows"][0]
    dest = r.get("destination_code")
    if not dest and r.get("destination_name"):
        codes = airports.resolve_codes(r["destination_name"])
        dest = codes[0] if codes else None
    return {"scheduled": r.get("scheduled"), "estimated": r.get("estimated"), "destination": dest,
            "destination_name": r.get("destination_name"), "airline": r.get("airline"),
            "gate": r.get("gate"), "remark": r.get("remark")}, None


@router.get("/plans/lookup")
def plan_lookup(airport: str = Query(..., max_length=3), flight_no: str = Query(..., max_length=10),
                date: Optional[str] = None):
    """계획 입력 화면: 편명을 넣으면 출발 시각과 체크인 마감 기준을 미리 보여 준다."""
    code = airport.strip().upper()
    if code not in airports.NAMES:
        return _error("BAD_ARGS", "지원하지 않는 공항입니다.", 400)
    found, why = flight_lookup(code, flight_no, _ymd(date))
    if not found:
        return ok({"found": False, "message": why})
    line = checkin.line_of(code, found["destination"])
    dep = found["estimated"] or found["scheduled"]
    return ok({"found": True, **found, "line": line, "checkin_min": checkin.minutes(line),
               "checkin_deadline": checkin.deadline(dep, checkin.minutes(line)) if dep else None})


def _save(body, pid=None):
    data = body.model_dump()
    data["airport"] = data["airport"].strip().upper()
    found, _ = (flight_lookup(data["airport"], data["flight_no"], _ymd(data.get("estimated"), data.get("user_arrival")))
                if data["airport"] in airports.NAMES else (None, None))
    try:
        return store.update(pid, data, found) if pid else store.create(data, found)
    except PlanError as e:
        return e


@router.post("/plans")
def create_plan(body: PlanIn):
    res = _save(body)
    return _plan_error(res) if isinstance(res, PlanError) else ok(res)


@router.get("/plans")
def list_plans():
    return ok(store.list())


@router.get("/plans/{pid}")
def get_plan(pid: int):
    plan = store.get(pid)
    if plan is None:
        return _error("NOT_FOUND", "계획을 찾을 수 없습니다.", 404)
    return ok(plan)


@router.put("/plans/{pid}")
def update_plan(pid: int, body: PlanIn):
    res = _save(body, pid)
    if isinstance(res, PlanError):
        return _plan_error(res)
    if res is None:
        return _error("NOT_FOUND", "계획을 찾을 수 없습니다.", 404)
    return ok(res)


@router.delete("/plans/{pid}")
def delete_plan(pid: int):
    if not store.delete(pid):
        return _error("NOT_FOUND", "계획을 찾을 수 없습니다.", 404)
    return ok({"deleted": pid})


# ---------------- 실행 로그 (F3014) ----------------
@router.get("/logs")
def list_logs():
    return ok(logs.list_sessions())


@router.get("/logs/{sid}")
def get_log(sid: str):
    try:
        events = logs.read(sid)
    except ValueError:
        return _error("BAD_ARGS", "세션 번호 형식이 올바르지 않습니다.", 400)
    if events is None:
        return _error("NOT_FOUND", "로그가 없습니다.", 404)
    return ok({"session_id": sid, "events": events})


@router.get("/logs/{sid}/audit")
def get_log_audit(sid: str):
    """실행(질문 1건)마다 이상 징후 판정. 판정은 규칙, 진단(diagnosis)은 이상 실행에만 붙는 LLM 결과로 따로 둔다."""
    try:
        if logs.read(sid) is None:
            return _error("NOT_FOUND", "로그가 없습니다.", 404)
        runs = logs.audit(sid)
    except ValueError:
        return _error("BAD_ARGS", "세션 번호 형식이 올바르지 않습니다.", 400)
    return ok({"session_id": sid, "runs": runs,
               "summary": {s: sum(1 for r in runs if r["status"] == s) for s in ("normal", "anomaly", "undetermined")}})


@router.delete("/logs/{sid}")
def delete_log(sid: str):
    try:
        removed = logs.delete(sid)
    except ValueError:
        return _error("BAD_ARGS", "세션 번호 형식이 올바르지 않습니다.", 400)
    if not removed:
        return _error("NOT_FOUND", "로그가 없습니다.", 404)
    return ok({"deleted": sid})
