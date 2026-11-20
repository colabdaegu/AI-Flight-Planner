"""셀프 체크리스트 자동 검증 (AI 코어 프로젝트 3회차 · 4장 검증).

프로젝트 폴더에서 가상환경을 켠 뒤 실행합니다. 서버를 따로 켤 필요는 없습니다 (같은 FastAPI 앱을 안에서 부릅니다).

    python verify/run_checks.py

- 실제 공공데이터 API 와 SSAFY GMS(LLM)를 부릅니다. 키는 .env 에서 읽고, 결과 파일에는 키를 남기지 않습니다.
- 체크리스트 번호(1~32)마다 실행 결과를 docs/verification/evidence/ 에 증빙 파일로 남기고,
  결과표를 docs/verification/RESULTS.md · results.json 에 씁니다.
- 대표 시나리오(4.7) 기록은 docs/verification/SCENARIOS.md, 실행 로그 샘플은 docs/samples/ 에 남깁니다.
- 결과는 '통과 / 실패 / 화면 확인 필요 / 해당없음' 중 하나입니다. 화면은 이 프로그램이 볼 수 없으므로
  화면이 필요한 항목은 API 쪽만 확인하고 '화면 확인 필요'로 남깁니다 (확인하지 않은 것을 통과로 적지 않음).
- 검증 중에 만든 계획은 끝나면 지웁니다. 실행 로그(세션 이름 verify-…)는 증빙으로 남깁니다.
"""
import importlib.metadata as md
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")          # Windows 콘솔에서도 한글이 깨지지 않게
except Exception:
    pass

from fastapi.testclient import TestClient            # noqa: E402

from app import audit, config                         # noqa: E402
from app.agent import graph, nodes                    # noqa: E402
from app.api import routes                            # noqa: E402
from app.events import bus                            # noqa: E402
from app.main import app                              # noqa: E402
from app.tools import common, flights, registry       # noqa: E402

OUT = os.path.join(ROOT, "docs", "verification")
EVID = os.path.join(OUT, "evidence")
SAMPLES = os.path.join(ROOT, "docs", "samples")
RUN = datetime.now().strftime("%m%d%H%M")
SLOW_MS = 20000            # NF3005: 대표 요청 1건이 20초 안이면 '사용에 지장 없음'으로 본다

PASS, FAIL, SCREEN, NA = "통과", "실패", "화면 확인 필요", "해당없음"

SPEC_VERSIONS = {          # 명세서 1.2 필수 라이브러리
    "langgraph": "1.2.10", "langchain": "1.3.14", "langchain-openai": "1.4.3", "langchain-core": "1.6.0",
    "langchain-text-splitters": "1.1.2", "openai": "2.54.0", "python-dotenv": "1.2.3", "requests": "2.34.2",
    "langsmith": "0.10.17", "typing-extensions": "4.16.0", "fastapi": "0.141.1", "uvicorn": "0.52.4",
    "pydantic": "2.13.5", "google-api-python-client": "2.201.0", "google-auth-oauthlib": "1.5.0",
    "google-auth-httplib2": "0.4.3", "chromadb": "1.5.9",
}


SCREENS = os.path.join(OUT, "screens")
SHOTS = {   # 화면이 필요한 항목: 이 이름의 캡처가 docs/verification/screens/ 에 있으면 '통과'로 본다
    20: ["20_plan_before.png", "20_plan_after.png"],      # 수정·삭제 전 목록 / 후 목록
    21: ["21_docs_paths.png", "21_docs_schemas.png", "21_network.png"],   # /docs 경로 목록 / 스키마 / 네트워크 탭
    22: ["22_replan.png", "22_pending.png", "22_toolfail.png", "22_nokey.png"],
}


def shots_missing(no):
    return [f for f in SHOTS.get(no, []) if not os.path.exists(os.path.join(SCREENS, f))]


# ---------------------------------------------------------------- 공통
def scrub(obj):
    """결과 파일에 키가 남지 않게 가린다 (NF3001)."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str) if not isinstance(obj, str) else obj
    for key in (config.DATA_GO_KR_KEY, config.GMS_API_KEY, config.GOOGLE_MAPS_API_KEY):
        if key:
            text = text.replace(key, "***").replace(quote(key, safe=""), "***")
    return text


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(scrub(obj))


def short(text, n=160):
    text = str(text or "").replace("\n", " / ")
    return text if len(text) <= n else text[:n] + "…"


def ev(trace, name):
    return [e for e in trace if e["event"] == name]


def tool_history(trace):
    out = []
    for e in trace:
        p = e["payload"]
        if e["event"] == "tool_called":
            out.append({"tool": p["tool"], "args": p["args"], "role": p.get("role")})
        elif e["event"] == "tool_result" and out:
            for c in reversed(out):
                if c["tool"] == p["tool"] and "ok" not in c:
                    c.update(ok=p["ok"], code=p.get("code"), elapsed_ms=p.get("elapsed_ms"), count=p.get("count"))
                    break
        elif e["event"] == "tool_reused":
            out.append({"tool": p["tool"], "args": p["args"], "reused": True})
    return out


def steps_line(trace):
    parts = []
    for e in trace:
        p = e["payload"]
        if e["event"] == "step_finished":
            parts.append(f"{p['step']}({p.get('elapsed_ms')}ms)")
        elif e["event"] == "replanned":
            parts.append(f"[재계획 {p['attempt']}: {p['reason']}]")
    return " → ".join(parts)


class Ctx:
    def __init__(self):
        self.client = TestClient(app)
        self.today = flights.today_kst()
        self.tomorrow = (datetime.strptime(self.today, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        self.runs = {}            # 이름 → {sid, message, data, ms}
        self.plans = []
        self.llm_calls = 0

    def sid(self, name):
        return f"verify-{RUN}-{name}"[:64]

    def chat(self, name, message, sid=None):
        sid = sid or self.sid(name)
        t0 = time.perf_counter()
        r = self.client.post("/api/chat", json={"message": message, "session_id": sid})
        ms = int((time.perf_counter() - t0) * 1000)
        body = r.json()
        data = body.get("data") or {}
        run = {"name": name, "sid": sid, "message": message, "status": r.status_code, "ok": body.get("ok"),
               "ms": ms, "data": data, "error": body.get("error")}
        self.runs[name] = run
        return run


def run_summary(run):
    d = run["data"]
    trace = d.get("trace") or []
    return {"session_id": run["sid"], "message": run["message"], "http": run["status"], "elapsed_ms": run["ms"],
            "intent": d.get("intent"), "intent_source": d.get("intent_source"), "replans": d.get("replans"),
            "steps": steps_line(trace), "tools": tool_history(trace), "answer": d.get("answer"),
            "audit": d.get("audit"), "error": run.get("error")}


# ---------------------------------------------------------------- 1~4 Tool
NORMAL_ARGS = {
    "search_departures": {"airport": "GMP"},
    "search_arrivals": {"airport": "GMP"},
    "get_airport_congestion": {"airport": "GMP"},
    "get_incheon_passenger_forecast": {"selectdate": "0"},
    "get_incheon_facilities": {"keyword": "약국"},
    "get_commercial_facilities": {"airport": "GMP"},
    "get_hourly_traffic": {"airport": "GMP"},
    "get_airport_overview": {"airport": "GMP"},
    "search_airports": {"query": "김포"},
}
BAD_ARGS = {
    "search_departures": {"airport": "ZZZ"},
    "search_arrivals": {"airport": "GMP", "date": "2026-13-45"},
    "search_flight": {"flight_no": ""},
    "get_flight_detail": {"airport": "GMP", "flight_no": "KE1", "direction": "sideways"},
    "get_airport_congestion": {"airport": "ICN"},
    "get_incheon_passenger_forecast": {"selectdate": "9"},
    "get_incheon_facilities": {"area": "X"},
    "get_commercial_facilities": {"airport": "ICN"},
    "get_hourly_traffic": {"airport": "ICN"},
    "get_airport_overview": {"airport": "ZZZ"},
    "search_airports": {"query": "김포", "scope": "moon"},
}


def envelope_ok(res):
    if not isinstance(res, dict) or not isinstance(res.get("ok"), bool):
        return False
    if res["ok"]:
        return "data" in res
    err = res.get("error") or {}
    return bool(err.get("code")) and isinstance(err.get("message"), str)


def call_safe(name, args):
    try:
        return registry.call(name, **args), None
    except Exception as e:                       # 도구가 예외를 밖으로 던지면 그 자체가 실패
        return None, f"{type(e).__name__}: {e}"


def c01(ctx):
    catalog = registry.catalog()
    doc = open(os.path.join(ROOT, "docs", "TOOLS.md"), encoding="utf-8").read()
    doc_names = set(re.findall(r"^### `([a-z_]+)`", doc, re.M))
    names = [t["name"] for t in catalog]
    # 편명이 필요한 도구는 실제 출발 목록에서 편명을 하나 얻어 쓴다
    dep = registry.call("search_departures", airport="GMP")
    first = next((f for f in (dep.get("data") or {}).get("flights", []) if f.get("flight_no")), None) if dep["ok"] else None
    fno = (first or {}).get("flight_no") or "KE1201"
    normal = dict(NORMAL_ARGS, search_flight={"flight_no": fno, "airport": "GMP"},
                  get_flight_detail={"airport": "GMP", "flight_no": fno})
    rows, problems = [], []
    saved_key = config.DATA_GO_KR_KEY
    for t in catalog:
        n = t["name"]
        row = {"tool": n, "kind": t["kind"], "in_doc": n in doc_names}
        for label, args in (("정상", normal.get(n, {})), ("잘못된 입력", BAD_ARGS.get(n, {}))):
            res, exc = call_safe(n, args)
            row[label] = {"args": args, "exception": exc, "envelope_ok": envelope_ok(res),
                          "ok": (res or {}).get("ok"), "code": ((res or {}).get("error") or {}).get("code"),
                          "message": ((res or {}).get("error") or {}).get("message"),
                          "count": ((res or {}).get("data") or {}).get("count") if (res or {}).get("ok") else None}
        config.DATA_GO_KR_KEY = ""
        common._cache.clear()
        try:
            res, exc = call_safe(n, normal.get(n, {}))
        finally:
            config.DATA_GO_KR_KEY = saved_key
            common._cache.clear()
        parts = ((res or {}).get("data") or {}).get("parts") if (res or {}).get("ok") else None
        row["키 비움"] = {"exception": exc, "envelope_ok": envelope_ok(res), "ok": (res or {}).get("ok"),
                        "code": ((res or {}).get("error") or {}).get("code"),
                        "parts": {k: (v.get("error") or {}).get("code") for k, v in (parts or {}).items()} or None}
        rows.append(row)
        if any(row[k]["exception"] for k in ("정상", "잘못된 입력", "키 비움")):
            problems.append(f"{n}: 예외가 밖으로 나옴")
        if not all(row[k]["envelope_ok"] for k in ("정상", "잘못된 입력", "키 비움")):
            problems.append(f"{n}: 응답 봉투 형식이 아님")
        if row["잘못된 입력"]["ok"] is not False:
            problems.append(f"{n}: 잘못된 입력이 실패로 돌아오지 않음")
        if t["kind"] == "external" and row["키 비움"]["code"] != "NO_KEY":
            problems.append(f"{n}: 키 비움이 NO_KEY 로 돌아오지 않음")
        if row["정상"]["ok"] is not True:
            problems.append(f"{n}: 정상 호출 실패({row['정상']['code']}: {row['정상']['message']})")
    missing_doc = sorted(set(names) - doc_names)
    if missing_doc:
        problems.append("Tool 정의서에 없는 도구: " + ", ".join(missing_doc))
    kinds = {t["kind"] for t in catalog}
    if kinds != {"external", "local"}:
        problems.append("외부/로컬 구분이 없음")
    evidence = {"agent_tool_list": catalog, "doc_tools": sorted(doc_names), "calls": rows}
    note = f"도구 {len(catalog)}개 (외부 {sum(t['kind'] == 'external' for t in catalog)} · 로컬 {sum(t['kind'] == 'local' for t in catalog)}). " \
           "로컬 도구는 키가 필요 없거나(공항 검색) 묶은 외부 도구의 실패를 parts 에 담아 돌려준다."
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or note


def c02(ctx):
    no_date = registry.call("search_departures", airport="GMP")
    future = (datetime.strptime(ctx.today, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d")
    past = (datetime.strptime(ctx.today, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d")
    far = registry.call("search_departures", airport="GMP", date=future)
    old = registry.call("search_departures", airport="GMP", date=past)
    chat = ctx.chat("c02-future", f"{future[4:6]}월 {future[6:]}일 김포에서 제주 가는 가장 빠른 편")
    ans = chat["data"].get("answer") or ""

    def brief(r):
        d = r.get("data") or {}
        return {"ok": r["ok"], "requested_date": d.get("requested_date"), "date": d.get("date"),
                "date_estimated": d.get("date_estimated"), "count": d.get("count"), "note": d.get("note"),
                "error": r.get("error")}
    problems = []
    if not (no_date["ok"] and no_date["data"]["date"] == ctx.today):
        problems.append("날짜 없이 호출했을 때 오늘이 기본값으로 쓰이지 않음")
    for label, r, want in (("미래", far, future), ("과거", old, past)):
        d = r.get("data") or {}
        if not (r["ok"] and d.get("requested_date") == want and d.get("date") != want and d.get("date_estimated")
                and "추정" in (d.get("note") or "")):
            problems.append(f"{label} 날짜: 요청·조회 기준이 다르다는 사실이 응답에 없음")
    if "추정" not in ans:
        problems.append("대화 응답에 추정 사실이 밝혀지지 않음")
    evidence = {"today": ctx.today, "no_date": brief(no_date), "future": brief(far), "past": brief(old),
                "chat": run_summary(chat)}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"기본값=오늘({ctx.today}). 범위 밖 날짜는 같은 요일 날짜로 조회하고 note·답변에 '추정'을 밝힘"


def c03(ctx):
    dep = registry.call("search_departures", airport="GMP", date=ctx.tomorrow)
    flights_ = (dep.get("data") or {}).get("flights") or []
    cand = next((f for f in flights_ if f.get("flight_no")), None)
    detail = registry.call("get_flight_detail", airport="GMP", flight_no=cand["flight_no"], date=ctx.tomorrow) if cand else None
    blank = dict(cand or {"flight_no": "TEST1"}, scheduled=None, estimated=None)
    judged = nodes.evaluate_flights({"flights": [blank]}, {"earliest": True},
                                    {"direction": "departure", "base": "GMP", "date": ctx.tomorrow, "other_codes": []})
    problems = []
    if not (detail and detail["ok"] and detail["data"]["count"] >= 1):
        problems.append("후보 목록의 편명으로 상세 조회가 이어지지 않음")
    if judged["candidates"] or not judged["rejected"] or "판단하지 않음" not in judged["rejected"][0]["reason"]:
        problems.append("시각이 빈 항목이 판단에 쓰임")
    evidence = {"candidate_from_list": cand, "detail_call": {"args": {"airport": "GMP", "flight_no": (cand or {}).get("flight_no"),
                                                                     "date": ctx.tomorrow}, "result": detail},
                "blank_attribute_case": {"input": blank, "candidates": len(judged["candidates"]), "rejected": judged["rejected"]}}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"{(cand or {}).get('flight_no')} → get_flight_detail {(detail or {}).get('data', {}).get('count')}건. 시각이 빈 편은 '판단하지 않음'으로 제외"


def c04(ctx):
    hit = registry.call("get_incheon_facilities", keyword="약국")
    miss = registry.call("get_incheon_facilities", keyword="아이스링크장")
    kac_hit = registry.call("get_commercial_facilities", airport="GMP", keyword="카페")
    kac_miss = registry.call("get_commercial_facilities", airport="GMP", keyword="아이스링크장")
    problems = []
    row = ((hit.get("data") or {}).get("rows") or [None])[0]
    if not (hit["ok"] and row and row.get("name") and row.get("location") and row.get("map_url")):
        problems.append("인천 시설 결과에 이름·위치·링크가 없음")
    for label, r in (("인천", miss), ("김포", kac_miss)):
        d = r.get("data") or {}
        if not (r["ok"] and d.get("count") == 0 and d.get("empty") and str(d.get("note", "")).startswith("결과 없음")):
            problems.append(f"{label} 결과 없음 형식이 정의와 다름")
    sample = lambda r: {"ok": r["ok"], "count": (r.get("data") or {}).get("count"), "note": (r.get("data") or {}).get("note"),
                        "first": ((r.get("data") or {}).get("rows") or [None])[0], "error": r.get("error")}
    evidence = {"icn_found": sample(hit), "icn_none": sample(miss), "gmp_found": sample(kac_hit), "gmp_none": sample(kac_miss)}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        "결과마다 name·location·hours·map_url. 결과 없음은 count 0 + empty true + note '결과 없음: …'"


# ---------------------------------------------------------------- 5~13 메인 Agent
def intent_event(run):
    e = ev(run["data"].get("trace") or [], "intent_analyzed")
    return e[0]["payload"] if e else {}


def c05(ctx):
    full = ctx.chat("c05-full", "내일 오후 3시 이후 김포에서 제주 가는 가장 빠른 편, 공항에는 1시 반에 도착해. 준비는 1시간이면 돼")
    part = ctx.chat("c05-partial", "제주 가는 편 알려줘")
    a, b = intent_event(full), intent_event(part)
    problems = []
    g = a.get("groups") or {}
    if not (g.get("기준", {}).get("date") == ctx.tomorrow and g.get("기준", {}).get("from_time") == "1500"):
        problems.append("기준 조건(날짜·시각) 추출 실패")
    if not (g.get("선호", {}).get("destination")):
        problems.append("선호 조건(목적지) 추출 실패")
    if not (g.get("제약", {}).get("arrive_airport_at") and g.get("제약", {}).get("buffer_min") == 60):
        problems.append("제약 조건(공항 도착·준비 시간) 추출 실패")
    for k in ("date", "from_time", "to_time", "arrive_airport_at", "buffer_min"):
        if k not in (b.get("empty") or []):
            problems.append(f"말하지 않은 {k} 가 채워짐")
    evidence = {"full_request": {"message": full["message"], "source": a.get("source"), "reason": a.get("reason"),
                                 "groups": a.get("groups"), "empty": a.get("empty")},
                "partial_request": {"message": part["message"], "source": b.get("source"), "reason": b.get("reason"),
                                    "groups": b.get("groups"), "empty": b.get("empty")}}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"분석 출처 {a.get('source')}. 빠진 요청은 date·시각·제약을 비워 둠"


def c06(ctx):
    hello = ctx.chat("c06-greeting", "안녕! 뭘 할 수 있어?")
    trip = ctx.runs.get("c05-full")
    struct = graph.structure()
    write(os.path.join(OUT, "graph.md"), "# LangGraph 노드 구성 (python -m app.agent.graph 출력)\n\n```mermaid\n" + struct + "\n```\n")
    agent_doc = open(os.path.join(ROOT, "docs", "AGENT.md"), encoding="utf-8").read()
    problems = []
    ht = hello["data"].get("trace") or []
    if ev(ht, "tool_called"):
        problems.append("인사 요청에서 도구를 부름")
    plan_h = ev(ht, "planned")
    plan_t = ev((trip or {}).get("data", {}).get("trace") or [], "planned")
    if not plan_t or not plan_t[0]["payload"]["steps"]:
        problems.append("출발 편 요청의 계획에 도구가 없음")
    for stage in graph.STAGES:
        if stage not in struct or stage not in agent_doc:
            problems.append(f"그래프·설계 문서 단계 불일치: {stage}")
    evidence = {"greeting": {"planned": plan_h[0]["payload"] if plan_h else None, "tool_calls": len(ev(ht, "tool_called")),
                             "answer": hello["data"].get("answer")},
                "trip_plan": plan_t[0]["payload"] if plan_t else None, "trip_tool_order": [c["tool"] for c in tool_history(trip["data"]["trace"])] if trip else None,
                "graph_mermaid": struct, "design_stages": list(graph.STAGES)}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        "인사 → 계획 steps=[] · 도구 0회. 출발 편 → search_departures(+보조) 순서. 그래프 노드 = AGENT.md 단계 (graph.md)"


def c07(ctx):
    chain = ctx.chat("c07-chain", "김포에서 제주 가는 가장 빠른 편")
    ct = chain["data"].get("trace") or []
    judged = ev(ct, "judged")
    chosen = judged[-1]["payload"].get("chosen") if judged else None
    detail_calls = [e["payload"] for e in ev(ct, "tool_called") if e["payload"]["tool"] == "get_flight_detail"]
    skipped = ev(ct, "detail_skipped")
    saved = set(config.VERIFY_FAIL_TOOLS)
    config.VERIFY_FAIL_TOOLS = {"get_airport_congestion", "get_incheon_passenger_forecast", "get_hourly_traffic"}
    common._cache.clear()
    try:
        broken = ctx.chat("c07-toolfail", "김포에서 제주 가는 가장 빠른 편")
    finally:
        config.VERIFY_FAIL_TOOLS = saved
    after = ctx.chat("c07-after", "김해에서 제주 가는 편")
    bt = broken["data"].get("trace") or []
    failed = [e["payload"] for e in ev(bt, "tool_result") if not e["payload"]["ok"]]
    problems = []
    if detail_calls and chosen and detail_calls[0]["args"]["flight_no"] != chosen:
        problems.append("판단에서 고른 편명이 상세 조회 입력으로 전달되지 않음")
    if not failed:
        problems.append("일부러 실패시킨 도구가 호출되지 않아 실패 실험이 성립하지 않음(보조 단계 없음)")
    if not (broken["ok"] and ev(bt, "responded") and broken["data"].get("answer")):
        problems.append("도구 실패 뒤 응답까지 진행되지 않음")
    if not (after["ok"] and after["data"].get("answer")):
        problems.append("이어지는 다른 요청이 정상 처리되지 않음")
    evidence = {"chain": {"chosen_by_judge": chosen, "detail_tool_args": detail_calls,
                          "detail_skipped": [e["payload"] for e in skipped], "tools": tool_history(ct)},
                "forced_failure": {"VERIFY_FAIL_TOOLS": sorted(["get_airport_congestion", "get_incheon_passenger_forecast", "get_hourly_traffic"]),
                                   "failed_results": failed, "steps": steps_line(bt), "answer": broken["data"].get("answer")},
                "next_request": run_summary(after)}
    how = "상세 조회 입력 = 판단에서 고른 편명" if detail_calls else "후보 목록에 탑승구가 있어 상세 조회 생략(detail_skipped) — 연결은 3번 항목에서 확인"
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"{how}. 보조 도구 실패({', '.join(sorted({f['tool'] for f in failed}))}) 기록 후 응답까지 진행, 다음 요청 정상"


def _hm_to_min(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def c08(ctx):
    run = ctx.chat("c08-osaka", "내일 인천공항에 9시 도착하는데 탈 수 있는 오사카행 항공편")
    ans = run["data"].get("answer") or ""
    trace = run["data"].get("trace") or []
    judged = ev(trace, "judged")
    crit = " ".join((judged[-1]["payload"].get("criteria") or []) if judged else [])
    problems = []
    m = re.search(r"공항 도착 (\d\d:\d\d) \+ 준비 (\d+)분 = (\d\d:\d\d) 이후", crit)
    pick = re.search(r"추천: .*? — \S+(?: \(.*?\))? (\d\d:\d\d)", ans)
    latest = re.search(r"늦어도 공항 도착 (\d\d:\d\d) \(출발 (\d\d:\d\d)에서 .*?여유 (\d+)분", ans)
    calc = {}
    if not m:
        problems.append("판단 기준에 계산식이 없음")
    else:
        arrive, buf, thr = m.group(1), int(m.group(2)), m.group(3)
        calc["threshold_ok"] = _hm_to_min(arrive) + buf == _hm_to_min(thr)
        if not calc["threshold_ok"]:
            problems.append("판단 기준의 계산 값이 틀림")
        dep_time = latest.group(2) if latest else (pick.group(1) if pick else None)   # 예상 출발이 있으면 그 시각
        if dep_time:
            calc["chosen_after_threshold"] = _hm_to_min(dep_time) >= _hm_to_min(thr)
            if not calc["chosen_after_threshold"]:
                problems.append("추천 편이 기준 시각보다 이름")
    if latest:
        calc["latest_ok"] = _hm_to_min(latest.group(2)) - int(latest.group(3)) == _hm_to_min(latest.group(1))
        if not calc["latest_ok"]:
            problems.append("역산한 공항 도착 시각이 틀림")
    elif pick:
        problems.append("역산 기준(늦어도 공항 도착)이 없음")
    if pick and "추정" not in ans:
        problems.append("API 로 확인할 수 없는 체크인 마감을 추정이라 밝히지 않음")
    evidence = {"run": run_summary(run), "criteria": crit, "checks": calc}
    note = "; ".join(problems) or (f"기준 {m.group(1)}+{m.group(2)}분={m.group(3)}, 추천 {pick.group(1) if pick else '-'}, "
                                   f"늦어도 도착 {latest.group(1) if latest else '-'}(역산), 체크인 마감은 '추정' 표기" if m else "")
    if not pick and not problems:
        note = "조건을 만족하는 편이 없어 추천 없음 — 계산식은 판단 기준에 표시됨"
    return (FAIL if problems else PASS), evidence, note


def c09(ctx):
    run = ctx.chat("c09-replan", "김포에서 제주 가는 가장 빠른 편, 23시 59분 이후로")
    trace = run["data"].get("trace") or []
    called = [(e["payload"]["tool"], json.dumps(e["payload"]["args"], sort_keys=True, ensure_ascii=False)) for e in ev(trace, "tool_called")]
    dup = len(called) != len(set(called))
    problems = []
    if not ev(trace, "replanned"):
        problems.append("재계획이 일어나지 않음 (23:59 이후 편이 있었음)")
    if dup:
        problems.append("같은 도구를 같은 입력으로 다시 부름")
    evidence = {"run": run_summary(run), "replanned": [e["payload"] for e in ev(trace, "replanned")],
                "reused": [e["payload"] for e in ev(trace, "tool_reused")], "duplicate_calls": dup}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"재계획 {run['data'].get('replans')}회, 재사용 {len(ev(trace, 'tool_reused'))}건, 같은 입력 재호출 없음"


def c10(ctx):
    run = ctx.chat("c10-limit", "빠른 시일 내에 김포에서 센다이 가는 편")
    trace = run["data"].get("trace") or []
    ans = run["data"].get("answer") or ""
    problems = []
    if run["data"].get("replans") != config.REPLAN_LIMIT:
        problems.append(f"재계획 {run['data'].get('replans')}회 (상한 {config.REPLAN_LIMIT}회와 다름)")
    if "상한" not in ans:
        problems.append("상한 도달 사유가 응답에 없음")
    evidence = {"run": run_summary(run), "replanned": [e["payload"] for e in ev(trace, "replanned")]}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"재계획 {config.REPLAN_LIMIT}회 후 멈춤, '재계획 상한에 도달' 안내 ({run['ms']}ms)"


def c11(ctx):
    sid = ctx.sid("c11-memory")
    first = ctx.chat("c11-first", "내일 김포에서 제주 가는 가장 빠른 편", sid=sid)
    second = ctx.chat("c11-second", "김해에서는?", sid=sid)

    def args_of(run):
        calls = [e["payload"]["args"] for e in ev(run["data"].get("trace") or [], "tool_called")
                 if e["payload"]["tool"] in ("search_departures", "search_arrivals")]
        return calls[0] if calls else {}
    a, b = args_of(first), args_of(second)
    inherited = intent_event(second).get("inherited")
    problems = []
    if b.get("airport") != "PUS":
        problems.append("바꾼 조건(출발 공항)이 갱신되지 않음")
    if a.get("destination") != b.get("destination") or a.get("date") != b.get("date"):
        problems.append("바꾸지 않은 조건(목적지·날짜)이 유지되지 않음")
    evidence = {"first": {"message": first["message"], "tool_args": a}, "second": {"message": second["message"], "tool_args": b,
                                                                                     "inherited": inherited}}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"출발 공항만 {a.get('airport')}→{b.get('airport')}, 목적지 {b.get('destination')}·날짜 {b.get('date')} 유지"


def c12(ctx):
    run = ctx.runs.get("c08-osaka") or ctx.chat("c08-osaka", "내일 인천공항에 9시 도착하는데 탈 수 있는 오사카행 항공편")
    ans = run["data"].get("answer") or ""
    judged = ev(run["data"].get("trace") or [], "judged")
    rejected = judged[-1]["payload"].get("rejected") if judged else 0
    need = ["추천:", "판단 기준:", "근거 자료:"]
    problems = [f"응답에 '{k}' 없음" for k in need if k not in ans]
    if rejected and "제외한 후보:" not in ans:
        problems.append("제외한 후보와 사유가 응답에 없음")
    support = "참고:" in ans
    evidence = {"answer": ans, "rejected_count": rejected, "has_support_info": support, "cards": run["data"].get("cards")}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"추천·판단 기준·근거 자료{'·보조 정보' if support else ''} 함께 표시, 제외 {rejected}편은 사유와 편명 표시"


def c13(ctx):
    return NA, {"domain": "항공 여행 일정 (건강·금융 아님)"}, "위험 도메인이 아님. 대신 '운항 정보는 실시간으로 바뀝니다' 안내를 붙임"


# ---------------------------------------------------------------- 14~18 로깅
def stored(ctx, sid):
    r = ctx.client.get(f"/api/logs/{sid}")
    return r.status_code, (r.json().get("data") or {}).get("events") or []


def same_events(trace, stored_events):
    tail = stored_events[-len(trace):] if trace else []
    return [(e["event"], e["payload"]) for e in trace] == [(e["event"], e["payload"]) for e in tail]


def c14(ctx):
    a, b = ctx.runs["c08-osaka"], ctx.runs["c09-replan"]
    out, problems = {}, []
    for run in (a, b):
        code, events = stored(ctx, run["sid"])
        trace = run["data"].get("trace") or []
        other = {e["session_id"] for e in events} - {run["sid"]}
        out[run["sid"]] = {"published": len(trace), "stored": len(events), "identical": same_events(trace, events),
                           "foreign_sessions": sorted(other),
                           "kinds": sorted({e["event"] for e in events})}
        if not out[run["sid"]]["identical"]:
            problems.append(f"{run['sid']}: 발행 이벤트와 저장 로그가 다름")
        if other:
            problems.append(f"{run['sid']}: 다른 세션 로그가 섞임")
        for need in ("step_started", "step_finished", "tool_called", "tool_result"):
            if need not in out[run["sid"]]["kinds"]:
                problems.append(f"{run['sid']}: {need} 이벤트 없음")
    llm = intent_event(a)
    timing = [e["payload"].get("elapsed_ms") for e in ev(a["data"]["trace"], "tool_result")]
    if not llm.get("reason"):
        problems.append("의도 분석 이벤트에 판단 이유가 없음")
    out["llm_decision"] = {"source": llm.get("source"), "intent": llm.get("intent"), "reason": llm.get("reason")}
    out["tool_elapsed_ms"] = timing
    note = "; ".join(problems) or f"세션 2개 모두 발행 = 저장, 섞임 없음. 의도 판단({llm.get('source')}) 이유: {short(llm.get('reason'), 60)}"
    if not problems and llm.get("source") != "llm":
        note += " (GMS 키가 없거나 실패해 규칙 분석 — LLM 판단 이유는 키가 있을 때 확인)"
    return (FAIL if problems else PASS), out, note


def c15(ctx):
    logger = routes.logs
    bus.unsubscribe(logger.on_event)
    try:
        run = ctx.chat("c15-nologger", "김포에서 제주 가는 가장 빠른 편")
    finally:
        bus.subscribe(logger.on_event)
    code, events = stored(ctx, run["sid"])
    problems = []
    if not (run["ok"] and run["data"].get("answer")):
        problems.append("로깅 Agent 없이 응답하지 못함")
    if events:
        problems.append("구독을 해제했는데 로그가 저장됨 (해제 실험이 성립하지 않음)")
    evidence = {"subscribers_during_run": 0, "answer": run["data"].get("answer"), "stored_log_http": code,
                "trace_events": len(run["data"].get("trace") or [])}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"구독 해제 상태로 정상 응답 ({run['ms']}ms), 저장된 로그 없음(HTTP {code})"


def c16(ctx):
    real = audit._llm
    calls = []
    message = "인천공항 면세점 어디 있어?"

    def counted(*a, **k):
        if any(message in str(x) for x in a):        # 다른 세션의 백그라운드 진단은 세지 않는다
            calls.append(1)
        return real(*a, **k)
    audit._llm = counted
    try:
        run = ctx.chat("c16-normal", message)
        time.sleep(0.5)
    finally:
        audit._llm = real
    src = open(os.path.join(ROOT, "app", "logger_agent.py"), encoding="utf-8").read()
    status = (run["data"].get("audit") or {}).get("status")
    problems = []
    if calls:
        problems.append(f"정상 세션 수집·저장 중 LLM {len(calls)}회 호출")
    if "ChatOpenAI" in src or "langchain_openai" in src:
        problems.append("로깅 Agent 코드가 LLM 클라이언트를 직접 씀")
    evidence = {"session": run["sid"], "audit_status": status, "logger_llm_calls": len(calls),
                "logger_imports_llm": "ChatOpenAI" in src}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"정상 세션(판정 {status}) 수집·저장 중 로깅 Agent LLM 호출 0회"


def c17(ctx):
    a = ctx.runs["c08-osaka"]
    code, events = stored(ctx, a["sid"])
    identical = same_events(a["data"].get("trace") or [], events)
    logger = routes.logs

    def broken(msg):
        raise RuntimeError("검증용 로그 저장 실패")
    bus.unsubscribe(logger.on_event)
    bus.subscribe(broken)
    try:
        run = ctx.chat("c17-brokenlog", "제주공항 지금 붐벼?")
    finally:
        bus.unsubscribe(broken)
        bus.subscribe(logger.on_event)
    problems = []
    if not identical:
        problems.append("저장된 로그가 발행된 원본 이벤트와 다름")
    if not (run["ok"] and run["data"].get("answer")):
        problems.append("로그 저장 실패 상태에서 대화가 멈춤")
    evidence = {"raw_vs_stored_identical": identical, "stored_sample": events[:6],
                "broken_store_run": {"answer": run["data"].get("answer"), "http": run["status"]}}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        "저장 로그 = 발행 이벤트 원본(요약·가공 없음). 저장 실패를 일으켜도 대화 정상"


def c18(ctx):
    c = ctx.client
    lst = c.get("/api/logs").json()
    sid = ctx.runs["c09-replan"]["sid"]
    detail = c.get(f"/api/logs/{sid}").json()
    events = (detail.get("data") or {}).get("events") or []
    ordered = [e["ts"] for e in events] == sorted(e["ts"] for e in events)
    gone_sid = ctx.runs["c06-greeting"]["sid"]
    deleted = c.delete(f"/api/logs/{gone_sid}")
    after = c.get(f"/api/logs/{gone_sid}")
    spec = c.get("/openapi.json").json()["paths"]
    log_methods = {p: sorted(v) for p, v in spec.items() if p.startswith("/api/logs")}
    edit = [p for p, v in log_methods.items() if {"put", "patch"} & set(v)] + \
           [p for p, v in log_methods.items() if "post" in v]
    problems = []
    if not (lst.get("ok") and any(s["session_id"] == sid for s in lst["data"])):
        problems.append("목록에 세션이 없음")
    if not (detail.get("ok") and ordered and events and events[0]["event"] == "request_received"):
        problems.append("상세가 실행 순서대로 오지 않음")
    if not (deleted.status_code == 200 and after.status_code == 404):
        problems.append("삭제가 되지 않음")
    if edit:
        problems.append("실행 로그 수정(생성) API 가 있음: " + ", ".join(edit))
    evidence = {"list_count": len(lst.get("data") or []), "list_sample": (lst.get("data") or [])[:3],
                "detail_first_events": [e["event"] for e in events[:8]], "ordered": ordered,
                "delete": {"session": gone_sid, "http": deleted.status_code, "after_get_http": after.status_code},
                "log_api_methods": log_methods}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        "GET 목록·GET 상세(순서대로)·DELETE 동작. /api/logs 경로에 PUT·PATCH·POST 없음"


# ---------------------------------------------------------------- 19~22 계획 · 서비스
def c19(ctx):
    c = ctx.client
    day = (datetime.strptime(ctx.today, "%Y%m%d") + timedelta(days=5)).strftime("%Y-%m-%d")
    body = {"title": "검증 계획 A", "memo": "자동 검증", "airport": "GMP", "flight_no": "ZZ901", "line": "domestic",
            "estimated": f"{day} 10:00", "dep_manual": True, "buffer_min": 60, "user_arrival": f"{day} 08:50"}
    a = c.post("/api/plans", json=body).json()
    b = c.post("/api/plans", json={**body, "title": "검증 계획 B (겹침)", "flight_no": "ZZ902", "estimated": f"{day} 10:30",
                                   "user_arrival": f"{day} 09:20"}).json()
    for r in (a, b):
        if r.get("ok"):
            ctx.plans.append(r["data"]["plan"]["id"])
    lst = c.get("/api/plans").json()
    one = c.get(f"/api/plans/{a['data']['plan']['id']}").json() if a.get("ok") else {}
    problems = []
    if not a.get("ok"):
        problems.append(f"계획 저장 실패: {a.get('error')}")
    else:
        p = a["data"]["plan"]
        for k in ("flight_no", "flight_date", "scheduled", "checkin_deadline", "user_arrival", "created_at"):
            if not p.get(k):
                problems.append(f"저장한 계획에 {k} 없음")
    if not (b.get("ok") and b["data"]["warnings"] and any("겹" in w for w in b["data"]["warnings"])):
        problems.append("겹치는 계획이 저장 결과와 함께 알려지지 않음")
    ids = [x["id"] for x in lst.get("data") or []]
    if not all(i in ids for i in ctx.plans):
        problems.append("목록에 저장한 계획이 없음")
    evidence = {"create_a": a, "create_b_overlap": b, "list_ids": ids, "detail_a": one}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"저장·목록·상세에 핵심 값과 생성 일시(created_at). 겹치는 B 저장 시 경고: {short((b.get('data') or {}).get('warnings'), 80)}"


def c20(ctx):
    c = ctx.client
    if len(ctx.plans) < 2:
        return FAIL, {}, "19번 계획 저장이 실패해 수정·삭제를 확인하지 못함"
    pa, pb = ctx.plans[:2]
    a = c.get(f"/api/plans/{pa}").json()["data"]
    upd = c.put(f"/api/plans/{pa}", json={"title": "검증 계획 A (수정)", "memo": "수정됨", "airport": "GMP", "flight_no": "ZZ901",
                                         "line": "domestic", "estimated": a["scheduled"][:11] + "13:00", "dep_manual": True,
                                         "buffer_min": 60, "user_arrival": a["scheduled"][:11] + "11:50"}).json()
    after_upd = c.get(f"/api/plans/{pa}").json().get("data") or {}
    dele = c.delete(f"/api/plans/{pb}")
    lst = [x["id"] for x in c.get("/api/plans").json().get("data") or []]
    js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    reload_after = js.count("loadPlans()") >= 2
    problems = []
    if not (upd.get("ok") and after_upd.get("title") == "검증 계획 A (수정)" and after_upd.get("scheduled", "").endswith("13:00")):
        problems.append("핵심 값 수정이 반영되지 않음")
    if not (dele.status_code == 200 and pb not in lst):
        problems.append("삭제가 목록에 반영되지 않음")
    if problems:
        return FAIL, {"update": upd, "list_after_delete": lst}, "; ".join(problems)
    evidence = {"update_response": upd, "after_update": after_upd, "delete_http": dele.status_code, "list_after_delete": lst,
                "screen_reloads_list_after_save_delete": reload_after, "screens": SHOTS[20]}
    if not shots_missing(20):
        return PASS, evidence, "API 수정·삭제 → 목록 반영 확인, 화면 캡처 전·후 2장(20_plan_before.png · 20_plan_after.png) 첨부"
    return SCREEN, evidence, ("API 수정·삭제와 목록 반영 확인. 화면(내 계획)에서 수정·삭제 전 목록(20_plan_before.png)과 "
                              "후 목록(20_plan_after.png) 캡처 2장을 docs/verification/screens/ 에 첨부하면 통과")


def c21(ctx):
    spec = ctx.client.get("/openapi.json").json()
    paths = spec["paths"]
    api_md = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    groups = {"대화": "/api/chat", "계획 관리": "/api/plans", "실행 로그": "/api/logs"}
    problems = [f"{k} 경로 없음" for k, p in groups.items() if p not in paths]
    for p in paths:
        short_p = p.replace("/api", "")
        if short_p.split("{")[0].rstrip("/") not in api_md:
            problems.append(f"API.md 에 없는 경로: {p}")
    calls = {k: bool(re.search(rf'api\(`?"?{re.escape(v.replace("/api", ""))}', js)) for k, v in groups.items()}
    if not all(calls.values()):
        problems.append("화면 코드가 백엔드 API 를 부르지 않는 화면이 있음")
    schemas = sorted(spec["components"]["schemas"])
    evidence = {"paths": {p: sorted(v) for p, v in paths.items()}, "schemas": schemas, "screen_calls_api": calls,
                "screens": SHOTS[21]}
    if problems:
        return FAIL, evidence, "; ".join(problems)
    if not shots_missing(21):
        return PASS, evidence, "대화·계획·로그 경로 분리, /docs 스키마, 화면이 /api/chat·/api/plans·/api/logs 호출 — 캡처 21_docs_paths.png·21_docs_schemas.png·21_network.png 첨부"
    return SCREEN, evidence, ("대화·계획·로그 경로 분리, /docs 에 요청·응답 스키마(ChatOut·PlanSavedOut·LogListOut 등) 표시, "
                              "화면 코드가 /api/chat·/api/plans·/api/logs 를 부름. /docs 경로 목록(21_docs_paths.png)·스키마(21_docs_schemas.png)와 "
                              "브라우저 네트워크 탭 캡처(21_network.png) 첨부하면 통과")


def c22(ctx):
    rep = ctx.runs["c10-limit"]["data"].get("trace") or []
    fail = ctx.runs["c07-toolfail"]["data"].get("trace") or []
    saved = config.DATA_GO_KR_KEY
    config.DATA_GO_KR_KEY = ""
    common._cache.clear()
    try:
        nokey = ctx.chat("c22-nokey", "김포 출발 시간표 보여줘")
    finally:
        config.DATA_GO_KR_KEY = saved
        common._cache.clear()
    nk = nokey["data"].get("trace") or []
    js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    problems = []
    if not ev(rep, "replanned"):
        problems.append("재계획 이벤트가 없음")
    if not [e for e in fail if e["event"] == "tool_result" and not e["payload"]["ok"]]:
        problems.append("도구 실패 이벤트가 없음")
    if not [e for e in nk if e["event"] == "tool_result" and e["payload"].get("code") == "NO_KEY"]:
        problems.append("키 비움 실행에서 NO_KEY 가 나오지 않음")
    if "설정 안내" not in (nokey["data"].get("answer") or ""):
        problems.append("키 누락 안내가 답변에 없음")
    ui = {"재계획 칩/색": "step-chip replan" in js or "replan" in js, "처리 중 표시": "pending" in js,
          "키 누락 배너": "DATA_GO_KR_KEY" in js, "도구 실패 배너": "불러오지 못했습니다" in js}
    if not all(ui.values()):
        problems.append("화면 코드에 표시 요소가 빠짐")
    evidence = {"replan_trace_steps": steps_line(rep), "toolfail_steps": steps_line(fail),
                "nokey": run_summary(nokey), "screen_code": ui, "screens": SHOTS[22]}
    if problems:
        return FAIL, evidence, "; ".join(problems)
    if not shots_missing(22):
        return PASS, evidence, "실행 순서·재계획 지점·처리 중·도구 실패·키 누락 안내를 이벤트와 화면 캡처 4장으로 확인"
    return SCREEN, evidence, ("실행 이벤트(순서·재계획·실패·NO_KEY)와 화면 코드 확인. 화면 캡처 4장 첨부하면 통과: "
                              "22_replan.png(재계획 주황 표시), 22_pending.png(처리 중), 22_toolfail.png(VERIFY_FAIL_TOOLS), "
                              "22_nokey.png(DATA_GO_KR_KEY 비움)")


# ---------------------------------------------------------------- 23~24 대표 시나리오
SCENARIOS = [
    ("S1", "한 번에 충족", "c08-osaka", "공항 도착 9:00+준비 120분 이후 출발 편 중 가장 이른 편 추천, 이른 편은 '준비 시간 부족'으로 제외, 재계획 없음"),
    ("S2", "재계획 필요", "c09-replan", "23:59 이후 편이 없어 시간 조건을 풀고(또는 다음 날로) 재계획 → 추천, 같은 입력 재호출 없음"),
    ("S3", "상한 도달", "c10-limit", "김포→센다이 편이 없어 재계획 3회 후 멈추고 '재계획 상한에 도달'과 조건 변경 제안"),
    ("S4", "Tool 하나 실패", "c07-toolfail", "보조 도구(혼잡도·승객 예고)를 일부러 실패시켜도 실패를 기록하고 출발 편 추천까지 진행"),
    ("S5", "Tool 불필요", "c06-greeting", "인사·사용법 → 계획에 도구 없음, 도구 호출 0회로 사용법 안내"),
    ("S6", "기준 다름(추정)", "c02-future", "자료 범위 밖 날짜 → 같은 요일 운항표로 조회하고 '추정'이라 밝힘"),
]


def judge_scenario(key, run):
    d = run["data"]
    trace = d.get("trace") or []
    ans = d.get("answer") or ""
    if key == "S1":
        return bool(ev(trace, "judged")) and not d.get("replans") and ("추천:" in ans or "찾지 못했습니다" in ans)
    if key == "S2":
        return d.get("replans", 0) >= 1 and "추천:" in ans
    if key == "S3":
        return d.get("replans") == config.REPLAN_LIMIT and "상한" in ans
    if key == "S4":
        return any(not e["payload"]["ok"] for e in ev(trace, "tool_result")) and bool(ev(trace, "responded"))
    if key == "S5":
        return not ev(trace, "tool_called") and bool(ans)
    if key == "S6":
        return "추정" in ans
    return False


def c23(ctx):
    rows = []
    for key, kind, name, expected in SCENARIOS:
        run = ctx.runs.get(name)
        if not run:
            rows.append({"id": key, "type": kind, "error": "실행되지 않음"})
            continue
        s = run_summary(run)
        ok = judge_scenario(key, run)
        rows.append({"id": key, "type": kind, "request": run["message"], "expected": expected, "matched": ok,
                     "elapsed_ms": run["ms"], "replans": s["replans"], "steps": s["steps"], "tools": s["tools"],
                     "answer": s["answer"], "session_id": run["sid"],
                     "diff": "없음" if ok else "기대와 다름 — 아래 실행 로그와 답변을 보고 원인 기록 필요"})
    write(os.path.join(OUT, "scenarios.json"), rows)
    md_ = ["# 대표 시나리오 검증 기록 (4.7 · NF3005)", "",
           f"실행: {datetime.now():%Y-%m-%d %H:%M} · `python verify/run_checks.py` · 실제 공공데이터 API + GMS", "",
           "| # | 유형 | 요청 | 재계획 | 처리 시간 | 기대와 일치 |", "|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            md_.append(f"| {r['id']} | {r['type']} | - | - | - | 실행 안 됨 |")
            continue
        md_.append(f"| {r['id']} | {r['type']} | {r['request']} | {r['replans']} | {r['elapsed_ms'] / 1000:.1f}초 | "
                   f"{'예' if r['matched'] else '아니오'} |")
    for r in rows:
        if "error" in r:
            continue
        md_ += ["", f"## {r['id']} {r['type']}", "", f"- 요청: {r['request']}", f"- 기대 동작: {r['expected']}",
                f"- 실행 단계: {r['steps']}", f"- 재계획: {r['replans']}회", f"- 처리 시간: {r['elapsed_ms']}ms",
                f"- 세션: `{r['session_id']}` (실행 로그 화면에서 같은 세션을 열 수 있음)", "- Tool 호출 이력:"]
        for t in r["tools"]:
            tag = "재사용" if t.get("reused") else ("성공" if t.get("ok") else f"실패 {t.get('code')}")
            md_.append(f"  - {t['tool']}({json.dumps(t['args'], ensure_ascii=False)}) → {tag}"
                       + (f", {t.get('count')}건" if t.get("count") is not None else "")
                       + (f", {t.get('elapsed_ms')}ms" if t.get("elapsed_ms") is not None else ""))
        md_ += ["- 최종 응답:", "", "```", r["answer"] or "", "```", f"- 기대와 실제의 차이: {r['diff']}"]
    write(os.path.join(OUT, "SCENARIOS.md"), "\n".join(md_) + "\n")
    missing = [r["id"] for r in rows if "error" in r]
    wrong = [r["id"] for r in rows if r.get("matched") is False]
    slow = [f"{r['id']} {r['elapsed_ms'] / 1000:.1f}초" for r in rows if r.get("elapsed_ms", 0) > SLOW_MS]
    problems = []
    if missing:
        problems.append("실행 안 된 시나리오: " + ", ".join(missing))
    if wrong:
        problems.append("기대와 다른 시나리오: " + ", ".join(wrong) + " (SCENARIOS.md 에 원인 기록 필요)")
    if slow:
        problems.append(f"{SLOW_MS // 1000}초 넘음: " + ", ".join(slow))
    times = ", ".join(f"{r['id']} {r['elapsed_ms'] / 1000:.1f}s" for r in rows if "elapsed_ms" in r)
    return (FAIL if problems else PASS), {"scenarios": rows}, "; ".join(problems) or f"6개 유형 모두 기대대로. 처리 시간 {times}"


def c24(ctx):
    os.makedirs(SAMPLES, exist_ok=True)
    out = {}
    for label, name in (("full_run_S1", "c08-osaka"), ("replan_S2", "c09-replan"), ("replan_limit_S3", "c10-limit"),
                        ("tool_failure_S4", "c07-toolfail")):
        run = ctx.runs.get(name)
        if not run:
            continue
        code, events = stored(ctx, run["sid"])
        path = os.path.join(SAMPLES, f"{label}.json")
        write(path, {"session_id": run["sid"], "request": run["message"], "answer": run["data"].get("answer"),
                     "elapsed_ms": run["ms"], "events": events})
        out[label] = {"file": os.path.relpath(path, ROOT).replace("\\", "/"), "events": len(events)}
    problems = []
    if "full_run_S1" not in out or not out["full_run_S1"]["events"]:
        problems.append("대표 요청 전체 로그 샘플 없음")
    if not any(k.startswith("replan") and v["events"] for k, v in out.items()):
        problems.append("재계획 사례 로그 샘플 없음")
    return (FAIL if problems else PASS), out, "; ".join(problems) or "docs/samples/ 에 " + ", ".join(v["file"].split("/")[-1] for v in out.values())


# ---------------------------------------------------------------- 25~27 비기능 · 제출
SKIP_DIRS = {".git", ".venv", "venv", "data", "__pycache__", ".pytest_cache", "node_modules"}


def c25(ctx):
    keys = {n: v for n, v in (("DATA_GO_KR_KEY", config.DATA_GO_KR_KEY), ("GMS_API_KEY", config.GMS_API_KEY),
                              ("GOOGLE_MAPS_API_KEY", config.GOOGLE_MAPS_API_KEY)) if v}
    hits, scanned = [], 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f == ".env":
                continue
            path = os.path.join(base, f)
            try:
                text = open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            scanned += 1
            for name, v in keys.items():
                if v in text or quote(v, safe="") in text:
                    hits.append(f"{os.path.relpath(path, ROOT)} ({name})")
    gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    c = ctx.client
    bad = c.get("/api/departures", params={"airport": "ZZZ"}).json()
    long_msg = c.post("/api/chat", json={"message": "가" * 600}).json()
    saved = config.DATA_GO_KR_KEY
    config.DATA_GO_KR_KEY = ""
    common._cache.clear()
    try:
        nokey = c.get("/api/departures", params={"airport": "GMP"}).json()
    finally:
        config.DATA_GO_KR_KEY = saved
        common._cache.clear()
    shown = json.dumps([bad, nokey], ensure_ascii=False)
    leaks = [w for w in ("Traceback", 'File "', "serviceKey=", *keys.values()) if w and w in shown]
    problems = []
    if hits:
        problems.append("키 문자열이 남은 파일: " + ", ".join(hits))
    if ".env" not in gi:
        problems.append(".env 가 .gitignore 에 없음")
    if leaks:
        problems.append("오류 응답에 상세·키가 보임")
    evidence = {"scanned_files": scanned, "key_found_in": hits, "env_ignored": ".env" in gi,
                "error_samples": {"bad_airport": bad, "no_key": nokey, "too_long_message_http422": "detail" in long_msg},
                "leaks": leaks}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"파일 {scanned}개에서 키 문자열 없음(.env 제외·.gitignore 포함). 오류 응답은 코드+안내 문구만"


def c26(ctx):
    need_dirs = ["web", "app/agent", "app/tools", "app/api", "app/store"]
    agent_files = sorted(f for f in os.listdir(os.path.join(ROOT, "app", "agent")) if f.endswith(".py"))
    tool_files = sorted(f for f in os.listdir(os.path.join(ROOT, "app", "tools")) if f.endswith(".py"))
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    words = {w: w in readme for w in ("출처", "이용 조건", "한도", "공공데이터포털", "약관")}
    problems = [f"{d} 없음" for d in need_dirs if not os.path.isdir(os.path.join(ROOT, d))]
    problems += [f"README 에 '{w}' 없음" for w, ok in words.items() if not ok]
    evidence = {"dirs": need_dirs, "agent_files": agent_files, "tool_files": tool_files, "readme_terms": words}
    return (FAIL if problems else PASS), evidence, "; ".join(problems) or \
        f"web/ ↔ app/ 분리, Agent {len(agent_files)}개·Tool {len(tool_files)}개 파일. README 에 출처·한도·이용 조건"


DELIVERABLES = {
    "요구사항 정의서(기획 검증 포함)": "docs/REQUIREMENTS.md", "Agent 설계 문서": "docs/AGENT.md",
    "Tool 정의서": "docs/TOOLS.md", "API 명세서": "docs/API.md", "실행 로그 샘플": "docs/samples",
    "README": "README.md", "검증 결과": "docs/VERIFICATION.md", "대표 시나리오 기록": "docs/verification/SCENARIOS.md",
    "셀프 체크리스트": "docs/셀프체크리스트.xlsx", "기획 검증 기록": "docs/verification/PLANNING_CHECK.md",
    "화면 캡처": "docs/verification/screens",
}
MADE_AT_END = {"검증 결과", "셀프 체크리스트", "기획 검증 기록"}     # 이 프로그램이 끝날 때 쓰는 파일


def c27(ctx):
    have = {k: os.path.exists(os.path.join(ROOT, v)) or k in MADE_AT_END for k, v in DELIVERABLES.items()}
    need = sorted({f for files in SHOTS.values() for f in files})
    lacking = [f for f in need if not os.path.exists(os.path.join(SCREENS, f))]
    failed_required = [r["no"] for r in RESULTS if r["no"] <= 26 and r["status"] == FAIL]
    screen_pending = [r["no"] for r in RESULTS if r["no"] <= 26 and r["status"] == SCREEN]
    missing = [k for k, ok in have.items() if not ok and k != "화면 캡처"]
    evidence = {"deliverables": have, "made_by_this_run": sorted(MADE_AT_END), "screens_needed": need,
                "screens_missing": lacking, "failed_required_items": failed_required}
    if failed_required or missing:
        return FAIL, evidence, "; ".join(filter(None, [
            ("실패한 필수 항목: " + ", ".join(map(str, failed_required))) if failed_required else "",
            ("없는 제출물: " + ", ".join(missing)) if missing else ""]))
    if lacking or screen_pending:
        return SCREEN, evidence, (f"필수 기능·제출 문서 확인. 화면 캡처 {len(need) - len(lacking)}/{len(need)}장 — "
                                  f"docs/verification/screens/ 에 {', '.join(lacking)} 를 넣고 다시 실행하면 통과")
    return PASS, evidence, f"필수 F3001~F3019 동작, 제출물 모두 있음, 화면 캡처 {len(need)}장"


# ---------------------------------------------------------------- 28 심화 F3020, 29~32
def fake_run(sid, kind):
    """이상 징후 규칙을 확인하려고 일부러 이상한 실행 기록을 만들어 발행한다 (메인 Agent 가 아니라 시험 데이터)."""
    def e(name, **p):
        bus.publish(name, sid, **p)
    e("request_received", message=f"[시험 데이터] {kind}", chars=10, has_memory=False)
    e("planned", attempt=0, steps=[{"tool": "search_departures", "args": {"airport": "GMP"}}], followups=[])
    if kind == "repeat":
        for _ in range(2):
            e("tool_called", tool="search_departures", args={"airport": "GMP"})
            e("tool_result", tool="search_departures", ok=True, elapsed_ms=10, count=3)
    elif kind == "unplanned":
        e("tool_called", tool="search_departures", args={"airport": "GMP"})
        e("tool_result", tool="search_departures", ok=True, elapsed_ms=10, count=3)
        e("tool_called", tool="get_incheon_facilities", args={"keyword": "약국"})
        e("tool_result", tool="get_incheon_facilities", ok=True, elapsed_ms=10, count=2)
    elif kind == "undetermined":
        e("tool_called", tool="search_departures", args={"airport": "GMP"})
        e("tool_result", tool="search_departures", ok=True, elapsed_ms=10, count=3)
        e("memory_updated", intent="departures", summary="")          # responded 없이 끝남 → 근거 부족
        return
    e("responded", chars=10, cards=0, replans=0)
    e("memory_updated", intent="departures", summary="")


def c28(ctx):
    logger = routes.logs
    real = audit._llm
    calls = {}

    def counted(*a, **k):
        calls[counted.sid] = calls.get(counted.sid, 0) + 1
        return real(*a, **k)
    counted.sid = None
    async_flag = logger.diagnose_async
    logger.diagnose_async = False              # 진단을 바로 끝내 결과와 LLM 호출 수를 함께 확인
    audit._llm = counted
    verdicts = {}
    try:
        for kind in ("repeat", "unplanned", "undetermined"):
            sid = ctx.sid(f"c28-{kind}")
            counted.sid = sid
            fake_run(sid, kind)
            verdicts[kind] = (logger.audits.latest(sid), sid)
        counted.sid = ctx.runs["c10-limit"]["sid"]
        verdicts["excessive_replan(실제 실행)"] = (logger.audit_last(ctx.runs["c10-limit"]["sid"]), ctx.runs["c10-limit"]["sid"])
        counted.sid = ctx.sid("c28-normal")
        normal = ctx.chat("c28-normal", "김포공항 시간대별로 언제 붐벼?")
        verdicts["normal(실제 실행)"] = (logger.audits.latest(normal["sid"]), normal["sid"])
        counted.sid = ctx.runs["c10-limit"]["sid"]
        follow = ctx.chat("c28-reference", "그럼 김해에서는?", sid=ctx.runs["c10-limit"]["sid"])
    finally:
        audit._llm = real
        logger.diagnose_async = async_flag
    ref = ev(follow["data"].get("trace") or [], "audit_referenced")
    table = {}
    for k, (v, sid) in verdicts.items():
        v = v or {}
        table[k] = {"session": sid, "status": v.get("status"), "warnings": [w["code"] for w in v.get("warnings") or []],
                    "diagnosis": v.get("diagnosis"), "llm_calls": calls.get(sid, 0)}
    want = {"repeat": "REPEATED_CALL", "unplanned": "UNPLANNED_CALL", "excessive_replan(실제 실행)": "EXCESSIVE_REPLAN"}
    problems = []
    for k, code in want.items():
        if code not in table[k]["warnings"]:
            problems.append(f"{k}: {code} 경고가 나오지 않음")
    if table["undetermined"]["status"] != "undetermined":
        problems.append("근거 이벤트가 없는 실행이 '판정 불가'로 남지 않음")
    if table["normal(실제 실행)"]["status"] != "normal" or table["normal(실제 실행)"]["llm_calls"]:
        problems.append("정상 실행이 이상 없음이 아니거나 LLM 이 호출됨")
    has_key = bool(config.GMS_API_KEY)
    for k in want:
        d = table[k]["diagnosis"] or {}
        expect = ("done", "failed", "pending") if has_key else ("skipped",)
        if d.get("status") not in expect:
            problems.append(f"{k}: 이상 판정 실행에 원인 진단이 붙지 않음 ({d.get('status')})")
    for k in ("repeat", "unplanned"):
        if has_key and not table[k]["llm_calls"]:
            problems.append(f"{k}: 이상 판정인데 LLM 진단 호출 0회")
    if not (ref and ref[0]["payload"]["status"] == "anomaly"):
        problems.append("메인 Agent 가 직전 판정을 참조하지 않음(audit_referenced)")
    evidence = {"verdicts": table, "main_agent_reference": [e["payload"] for e in ref],
                "note": "repeat·unplanned·undetermined 는 규칙 확인용 시험 기록, excessive_replan·normal 은 실제 실행"}
    note = "; ".join(problems) or ("반복·재계획 과다·계획 외 호출 각각 경고, 근거 없음은 판정 불가, 정상 실행 LLM 0회, "
                                   f"이상 실행만 진단({'LLM' if has_key else '키 없음 → skipped'}), 다음 질문에서 audit_referenced")
    return (FAIL if problems else PASS), evidence, note


def c_na(no):
    def fn(ctx):
        return NA, {}, "선택하지 않은 심화 기능 (F3020 만 선택)"
    return fn


# ---------------------------------------------------------------- 실행
CHECKS = [
    (1, "F3001", "Tool 명세·등록, 정상/실패 호출 형식", c01),
    (2, "F3002", "기준 날짜 기본값, 요청·조회 기준 차이 명시", c02),
    (3, "F3002 · F3003", "식별자로 상세 조회 연결, 빈 속성은 판단하지 않음", c03),
    (4, "F3004", "보조 정보 이름·주소·링크, 결과 없음 형식", c04),
    (5, "F3005", "기준·선호·제약 추출, 없는 조건은 비움", c05),
    (6, "F3006", "계획 구성, 도구 불필요 요청, 그래프 노드", c06),
    (7, "F3007 · NF3003", "앞 결과 → 다음 입력, Tool 실패해도 진행", c07),
    (8, "F3008", "계산 값과 판단 기준 일치, 확인 불가 값은 역산·추정 표시", c08),
    (9, "F3009", "재계획과 결과 재사용", c09),
    (10, "F3009 · NF3004", "재계획 상한에서 멈춤", c10),
    (11, "F3010", "대화 맥락: 바뀐 조건만 갱신", c11),
    (12, "F3011", "추천·보조 정보·판단 기준·근거·제외 사유", c12),
    (13, "F3011", "위험 도메인 안내 (해당 도메인만)", c13),
    (14, "F3012 · F3013", "이벤트 발행·세션 저장·LLM 판단 이유", c14),
    (15, "F3012", "로깅 Agent 없이 메인 Agent 동작", c15),
    (16, "F3013", "로깅 Agent 수집·저장에 LLM 미사용", c16),
    (17, "F3013", "원본 그대로 저장, 저장 실패해도 대화 동작", c17),
    (18, "F3014", "로그 목록·상세·삭제, 수정 API 없음", c18),
    (19, "F3015", "계획 저장·조회, 겹침 알림", c19),
    (20, "F3016", "계획 수정·삭제와 화면 반영", c20),
    (21, "F3017 · F3018", "API 경로·형식, 화면이 API 호출", c21),
    (22, "F3019", "실행 단계·재계획 지점·처리 중·실패·키 누락 안내", c22),
    (23, "필수 · NF3005", "대표 시나리오 5종 이상과 처리 시간", c23),
    (24, "필수", "실행 로그 샘플 (전체 1건 + 재계획)", c24),
    (25, "NF3001 · NF3002", "키 미포함, 화면에 키·오류 상세 미노출", c25),
    (26, "NF3006 · NF3007", "모듈 분리, 출처·이용 조건 기록", c26),
    (28, "F3020", "이상 징후 판단 (심화)", c28),
    (27, "필수", "제출 전 점검", c27),
    (29, "F3021", "Planner·Worker (심화)", c_na(29)),
    (30, "F3021", "Worker LLM 조건 (심화)", c_na(30)),
    (31, "F3022", "RAG 검색 Tool (심화)", c_na(31)),
    (32, "F3023", "캘린더 연동 Tool (심화)", c_na(32)),
]
RESULTS = []


def versions():
    rows = []
    for pkg, want in SPEC_VERSIONS.items():
        try:
            got = md.version(pkg)
        except md.PackageNotFoundError:
            got = None
        rows.append({"package": pkg, "spec": want, "installed": got,
                     "state": "일치" if got == want else ("미설치" if got is None else "다름")})
    lines = ["# 필수 라이브러리 버전 대조 (명세서 1.2)", "", "| 패키지 | 명세 | 설치됨 | 상태 |", "|---|---|---|---|"]
    lines += [f"| {r['package']} | {r['spec']} | {r['installed'] or '-'} | {r['state']} |" for r in rows]
    lines += ["", "google-api-python-client · google-auth-* · chromadb · langchain-text-splitters 는 심화 F3022 · F3023 용이며 "
              "이 프로젝트는 F3020 만 선택해 쓰지 않습니다."]
    write(os.path.join(OUT, "versions.md"), "\n".join(lines) + "\n")
    return rows


# ---------------------------------------------------------------- 기획 검증 (명세 2.7) 기록
def planning_check(ctx):
    """외부 API 호출 확인 · LLM 연결과 Tool 선택 · 대표 요청 설계 시험을 docs/verification/PLANNING_CHECK.md 에 남긴다."""
    import subprocess
    lines = ["# 기획 검증 기록 (명세 2.7)", "", f"실행 {datetime.now():%Y-%m-%d %H:%M} · `python verify/run_checks.py` 가 함께 만든 기록입니다.", ""]
    # 1) 외부 API 를 코드로 한 번씩 호출 (api_check/check_apis.py, 한도 보호를 위해 한국공항공사는 김포·제주만)
    lines += ["## 1. 외부 API 호출 확인", "", "`python api_check/check_apis.py --group all --airport GMP CJU` 출력 (키는 가림):", ""]
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "api_check", "check_apis.py"), "--group", "all",
                              "--airport", "GMP", "CJU"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=300, cwd=ROOT)
        text = scrub((out.stdout or "") + (("\n" + out.stderr) if out.stderr else ""))
    except Exception as e:
        text = f"실행하지 못함: {type(e).__name__}: {e}"
    write(os.path.join(OUT, "api_check.txt"), text)
    counts = {k: len(re.findall(rf"\b{k}\b", text)) for k in ("OK", "NO_RESULT", "CHECK", "FAIL")}
    lines += [f"- 판정 수(출력 안 단어 수 기준): " + " · ".join(f"{k} {v}" for k, v in counts.items()),
              "- 전체 출력: [api_check.txt](api_check.txt)", ""]
    # 2) LLM 연결과 Tool 선택
    lines += ["## 2. LLM 연결 · Tool 명세를 넘겼을 때 Tool 선택", ""]
    if not config.GMS_API_KEY:
        lines += ["- GMS_API_KEY 가 없어 확인하지 못함 (.env 에 키를 넣고 다시 실행)", ""]
    else:
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=config.GMS_MODEL, api_key=config.GMS_API_KEY, base_url=config.GMS_BASE_URL,
                             timeout=30, max_retries=1)
            t0 = time.perf_counter()
            hello = llm.invoke("한 단어로 답해: 연결 확인")
            ms = int((time.perf_counter() - t0) * 1000)
            lines.append(f"- LLM 호출 성공 ({config.GMS_MODEL}, {ms}ms): {short(getattr(hello, 'content', hello), 60)}")
            tools_spec = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["parameters"]}} for t in registry.catalog()]
            asks = ["김포에서 제주 가는 가장 빠른 편 알려줘", "KE1205 지금 탑승구 어디야?", "인천공항 출국장 약국 어디 있어?", "안녕!"]
            lines += ["", "| 요청 | LLM 이 고른 Tool (인자) |", "|---|---|"]
            for q in asks:
                r = llm.bind_tools(tools_spec).invoke(q)
                picked = ", ".join(f"{c['name']}({json.dumps(c.get('args'), ensure_ascii=False)})" for c in (r.tool_calls or [])) or "Tool 없이 답함"
                lines.append(f"| {q} | {picked} |")
            lines.append("")
        except Exception as e:
            lines += [f"- LLM 확인 실패: {type(e).__name__} (키·접속 주소·패키지 설치를 확인)", ""]
    # 3) 설계 시험: 대표 요청을 설계한 단계·Tool 순서대로 따라가며 입력이 앞 단계 결과로 채워지는지
    lines += ["## 3. 설계 시험 (대표 요청을 단계별로 따라가기)", ""]
    for name in ("c08-osaka", "c09-replan", "c10-limit"):
        run = ctx.runs.get(name)
        if not run:
            continue
        trace = run["data"].get("trace") or []
        it = intent_event(run)
        lines += [f"### {run['message']}", "", f"- 분석(기준·선호·제약): `{json.dumps(it.get('groups'), ensure_ascii=False)}`",
                  f"- 비워 둔 조건: {', '.join(it.get('empty') or []) or '없음'}"]
        for e in trace:
            p = e["payload"]
            if e["event"] == "planned":
                lines.append(f"- 계획(시도 {p.get('attempt')}): " + " → ".join(f"{s['tool']}{json.dumps(s['args'], ensure_ascii=False)}" for s in p.get("steps") or []))
            elif e["event"] == "replanned":
                lines.append(f"- 재계획 {p['attempt']}: {p['reason']}")
            elif e["event"] == "judged":
                lines.append(f"- 판단: 후보 {p.get('candidates')} · 제외 {p.get('rejected')} {p.get('rejected_reasons') or ''} · 선택 {p.get('chosen') or '-'}")
            elif e["event"] == "tool_called" and p["tool"] == "get_flight_detail":
                lines.append(f"- 앞 단계 결과 → 다음 입력: get_flight_detail{json.dumps(p['args'], ensure_ascii=False)}")
        lines.append("")
    write(os.path.join(OUT, "PLANNING_CHECK.md"), "\n".join(lines) + "\n")


# ---------------------------------------------------------------- 셀프 체크리스트 · 검증 결과 문서
CHECKLIST_TEMPLATE = os.path.join(ROOT, "verify", "checklist_template.xlsx")
CHECKLIST_OUT = os.path.join(ROOT, "docs", "셀프체크리스트.xlsx")
XLSX_STATUS = {PASS: "통과", FAIL: "실패", SCREEN: "미확인", NA: "해당없음"}


def fill_checklist():
    """제공된 셀프 체크리스트 양식에 자동 검증 결과를 적는다. 화면 캡처가 없는 항목은 '미확인'으로 남긴다."""
    try:
        import openpyxl
    except ImportError:
        print("openpyxl 이 없어 셀프 체크리스트(xlsx)는 쓰지 못했습니다: python -m pip install openpyxl")
        return False
    wb = openpyxl.load_workbook(CHECKLIST_TEMPLATE)
    ws = wb.active
    ws["C3"] = "셀프"
    ws["C4"] = "김현수 (SSAFY 16기)"
    ws["C5"] = "김현수 · 자동 검증 verify/run_checks.py"
    ws["C6"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    for r in RESULTS:
        row = 8 + r["no"]
        if ws.cell(row, 3).value is None or int(ws.cell(row, 3).value) != r["no"]:
            continue
        ws.cell(row, 7).value = XLSX_STATUS[r["status"]]
        files = [r["evidence"]] if r["evidence"] else []
        files += [f"docs/verification/screens/{f}" for f in SHOTS.get(r["no"], [])
                  if os.path.exists(os.path.join(SCREENS, f))]
        ws.cell(row, 8).value = "\n".join(files) or "-"
        note = r["note"]
        if r["status"] == SCREEN:
            note = "화면 캡처 첨부 전: " + note
        ws.cell(row, 9).value = None if r["status"] == PASS else note     # 비고는 실패·미확인·해당없음 사유만
    wb.save(CHECKLIST_OUT)
    return True


def write_verification_doc(count, total_s):
    scen = []
    try:
        scen = json.load(open(os.path.join(OUT, "scenarios.json"), encoding="utf-8"))
    except Exception:
        pass
    shots = sorted(f for f in os.listdir(SCREENS) if f.lower().endswith((".png", ".jpg"))) if os.path.isdir(SCREENS) else []
    L = ["# 검증 결과 (명세 4장 · 6장 5)", "",
         f"`python verify/run_checks.py` 를 {datetime.now():%Y-%m-%d %H:%M} 에 실행한 결과입니다 (실제 공공데이터 API + SSAFY GMS, 총 {total_s:.0f}초). "
         "같은 FastAPI 앱을 그대로 불러 요청을 보내고, 항목마다 실행 결과를 증빙 파일로 남겼습니다.", "",
         "**요약:** " + " · ".join(f"{k} {v}" for k, v in count.items()), "",
         "| 문서 | 내용 |", "|---|---|",
         "| [verification/RESULTS.md](verification/RESULTS.md) | 셀프 체크리스트 1~32번 항목별 결과·비고 |",
         "| [verification/evidence/](verification/evidence/) | 항목별 실행 결과(요청·응답·이벤트) |",
         "| [verification/SCENARIOS.md](verification/SCENARIOS.md) | 대표 시나리오 6개: 최종 응답, 실행 단계, Tool 호출 이력, 재계획, 처리 시간, 기대와 실제의 차이 |",
         "| [samples/](samples/) | 실행 로그 샘플: 대표 요청 전체(full_run_S1), 재계획(replan_S2), 상한 도달(replan_limit_S3), 도구 실패(tool_failure_S4) |",
         "| [verification/PLANNING_CHECK.md](verification/PLANNING_CHECK.md) | 기획 검증: 외부 API 호출, LLM 연결·Tool 선택, 설계 시험 |",
         "| [verification/graph.md](verification/graph.md) | LangGraph 노드 구성 (분석 → 계획 → 실행 → 판단 → 응답) |",
         "| [verification/versions.md](verification/versions.md) | 명세 1.2 필수 라이브러리 버전 대조 |",
         "| [셀프체크리스트.xlsx](셀프체크리스트.xlsx) | 제공 양식에 결과·증빙·비고를 적은 셀프 체크리스트 |", "",
         "## 항목별 결과", "", "| 번호 | 요구사항 | 확인 항목 | 결과 | 비고 |", "|---|---|---|---|---|"]
    for r in RESULTS:
        L.append(f"| {r['no']} | {r['requirement']} | {r['item']} | {r['status']} | {r['note'].replace('|', '/')} |")
    L += ["", "## 대표 시나리오 요약 (4.7)", "", "| # | 유형 | 요청 | 재계획 | 처리 시간 | 기대와 일치 |", "|---|---|---|---|---|---|"]
    for r in scen:
        if "error" in r:
            continue
        L.append(f"| {r['id']} | {r['type']} | {r['request']} | {r['replans']} | {r['elapsed_ms'] / 1000:.1f}초 | {'예' if r['matched'] else '아니오'} |")
    L += ["", "## 화면 캡처", ""]
    if shots:
        L += [f"![{f}](verification/screens/{f})" for f in shots]
    else:
        L.append("아직 없습니다. README 의 '검증' 절에 있는 순서대로 9장을 찍어 `docs/verification/screens/` 에 넣고 다시 실행하십시오.")
    write(os.path.join(ROOT, "docs", "VERIFICATION.md"), "\n".join(L) + "\n")


def main():
    os.makedirs(EVID, exist_ok=True)
    os.makedirs(os.path.join(OUT, "screens"), exist_ok=True)
    ctx = Ctx()
    started = time.perf_counter()
    print(f"검증 시작 {datetime.now():%Y-%m-%d %H:%M:%S} · 오늘(KST) {ctx.today} · "
          f"공공데이터 키 {'있음' if config.DATA_GO_KR_KEY else '없음'} · GMS 키 {'있음' if config.GMS_API_KEY else '없음'}")
    ver = versions()
    for no, req, title, fn in CHECKS:
        t0 = time.perf_counter()
        try:
            status, evidence, note = fn(ctx)
        except Exception as e:
            status, evidence = FAIL, {"exception": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=6)}
            note = f"검증 코드 실행 중 오류: {type(e).__name__}: {short(str(e), 120)}"
        ms = int((time.perf_counter() - t0) * 1000)
        file = f"{no:02d}_{req.split(' ')[0].replace('·', '')}.json" if status != NA else ""
        if file:
            write(os.path.join(EVID, file), {"no": no, "requirement": req, "item": title, "status": status, "note": note,
                                             "elapsed_ms": ms, "evidence": evidence})
        RESULTS.append({"no": no, "requirement": req, "item": title, "status": status,
                        "evidence": f"docs/verification/evidence/{file}" if file else "", "note": note, "elapsed_ms": ms})
        print(f"[{no:2d}] {status:8s} {req:16s} {title} — {short(note, 110)}")
    # 계획은 지운다 (실행 로그는 증빙으로 남긴다)
    for pid in ctx.plans:
        ctx.client.delete(f"/api/plans/{pid}")
    RESULTS.sort(key=lambda r: r["no"])
    count = {s: sum(r["status"] == s for r in RESULTS) for s in (PASS, FAIL, SCREEN, NA)}
    write(os.path.join(OUT, "results.json"), {"run_at": datetime.now().isoformat(timespec="seconds"), "today": ctx.today,
                                              "keys": {"DATA_GO_KR_KEY": bool(config.DATA_GO_KR_KEY), "GMS_API_KEY": bool(config.GMS_API_KEY)},
                                              "summary": count, "results": RESULTS, "versions": ver})
    lines = ["# 셀프 체크리스트 자동 검증 결과", "", f"실행 {datetime.now():%Y-%m-%d %H:%M} · 총 {time.perf_counter() - started:.0f}초 · "
             + " · ".join(f"{k} {v}" for k, v in count.items()), "",
             "| 번호 | 요구사항 | 확인 항목 | 결과 | 증빙 | 비고 |", "|---|---|---|---|---|---|"]
    for r in RESULTS:
        ev_link = f"[{r['evidence'].split('/')[-1]}](evidence/{r['evidence'].split('/')[-1]})" if r["evidence"] else "-"
        lines.append(f"| {r['no']} | {r['requirement']} | {r['item']} | {r['status']} | {ev_link} | {r['note'].replace('|', '/')} |")
    write(os.path.join(OUT, "RESULTS.md"), "\n".join(lines) + "\n")
    try:
        planning_check(ctx)
    except Exception as e:
        print("기획 검증 기록을 쓰지 못했습니다:", type(e).__name__, e)
    write_verification_doc(count, time.perf_counter() - started)
    if fill_checklist():
        print("셀프 체크리스트: docs/셀프체크리스트.xlsx")
    print("\n" + " · ".join(f"{k} {v}" for k, v in count.items()))
    print(f"결과: {os.path.relpath(os.path.join(OUT, 'RESULTS.md'), ROOT)}  (총 {time.perf_counter() - started:.0f}초)")


if __name__ == "__main__":
    main()
