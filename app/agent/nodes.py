"""메인 에이전트 노드 (명세 F3005 ~ F3011). 각 노드는 state 를 받아 바뀐 키만 돌려주고 emit 으로 이벤트를 남긴다.

analyze → plan → execute → judge ─┬─ replan → plan   (조건을 만족하는 후보가 없을 때, 최대 REPLAN_LIMIT 회)
                                  ├─ enrich → respond (고른 후보의 편명으로 상세 조회)
                                  └─ respond
- analyze : 요청에서 조건 추출(LLM 또는 규칙) + 직전 대화 조건 병합. 말하지 않은 값은 채우지 않는다.
- plan    : 의도에 맞는 도구와 순서를 정한다. 기본값을 쓴 경우 '가정'으로 기록해 답에 밝힌다.
- execute : 계획대로 도구를 부르고, 같은 인자의 호출은 이미 받은 결과를 재사용한다.
- judge   : 제약 판단(시각·준비 시간·결항). 후보가 없으면 시간 조건 해제 → 다음 날 순으로 넓혀 재계획.
- enrich  : 앞 도구가 고른 후보의 편명을 다음 도구(상세 조회) 입력으로 넘긴다.
- respond : 추천 · 판단 기준 · 제외 사유 · 가정 · 근거 자료를 함께 답한다.
"""
import json
import re
import time
from datetime import datetime, timedelta

from app import airports, checkin, config
from app.agent import cancel
from app.agent.llm import extract, now_kst
from app.tools.registry import call

FMT = "%Y-%m-%d %H:%M"
BUFFER_DEFAULT = {"international": 120, "domestic": 60}   # 항공사 일반 안내 기준. 쓸 때마다 '기본값'으로 밝힌다.
FATAL = ("NO_KEY", "BAD_ARGS", "NOT_FOUND")               # 다시 해도 결과가 같은 실패: 재계획하지 않는다
CARRY = ("airport", "destination", "origin", "flight_no", "date", "flexible_date", "from_time", "to_time",
         "earliest", "arrive_airport_at", "buffer_min", "keyword")
FOLLOWUP = re.compile(r"(다른|다음|그럼|그거|그 편|그편|말고|대신|은\?|는\?|이면|라면|거기)")

SOURCES = {
    "IIAC": "인천국제공항공사 여객기 운항 현황",
    "KAC": "한국공항공사 실시간 운항 정보",
    "get_airport_congestion": "한국공항공사 공항 혼잡도(실시간)",
    "get_hourly_traffic": "한국공항공사 시간대별 이용 흐름",
    "get_incheon_passenger_forecast": "인천국제공항공사 승객 예고",
    "get_incheon_facilities": "인천국제공항공사 상업시설 정보",
    "get_commercial_facilities": "한국공항공사 전국공항 임대계약 정보",
    "get_flight_detail": "운항 상세 (편명 조회)",
}

GREETING = ("안녕하십니까. 이런 질문에 답할 수 있습니다.\n"
            "- 출발 편 찾기: '센다이 가는 가장 빠른 편', '내일 김포에서 제주 오후 3시 이후'\n"
            "- 공항 도착 시각 기준 판단: '인천공항에 9시 도착하면 탈 수 있는 오사카행'\n"
            "- 도착 편: '인천 도착하는 나리타발 항공편'\n"
            "- 편명 조회: 'KE123 지금 어디야', 'OZ102 탑승구'\n"
            "- 혼잡도·예상 승객: '김포 혼잡도', '인천 지금 붐벼?'\n"
            "- 시설: '인천공항 출국장 약국', '면세점 위치'\n"
            "같은 대화 안에서는 '내일은?', '김포에서는?'처럼 바꾼 조건만 말해도 이어서 찾습니다.")
HELP = ("공항과 항공편에 관한 질문만 답할 수 있습니다. 예: '센다이 가는 가장 빠른 편', "
        "'김포 출발 시간표', 'KE123 상태', '제주 혼잡도', '인천공항 면세점'")
PLAN_HELP = ("체크인 마감은 '출발(예상 출발이 있으면 그 시각) − 준비 시간'으로 계산합니다. "
             "'내 계획' 화면에서 편명, 출발 시각, 준비 시간(분), 내 공항 도착 예정 시각을 넣으면 "
             "도착이 마감보다 늦은지 판단하고, 같은 날 준비 시간이 겹치는 계획이 있으면 알려 드립니다. "
             "대화에서 '인천공항 9시 도착인데 탈 수 있는 오사카행?'처럼 물어도 같은 기준으로 판단합니다.")


# ---------------- 공통 ----------------
def _today():
    return now_kst().strftime("%Y%m%d")


def _shift(ymd, days):
    return (datetime.strptime(ymd, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _fmt_ymd(ymd):
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if ymd and len(ymd) == 8 else (ymd or "")


def _hm(value):
    return value[11:16] if value and len(value) >= 16 else (value or "-")


def _parse(value):
    try:
        return datetime.strptime(value, FMT)
    except (TypeError, ValueError):
        return None


def _codes(text):
    out = []
    for part in str(text or "").split(","):
        for c in airports.resolve_codes(part):
            if c not in out:
                out.append(c)
    return out


def _intl(codes, base):
    if codes:
        return not all(airports.is_domestic(c) for c in codes)
    return base == airports.ICN


def _buffer(it, codes, base):
    kind = "international" if _intl(codes, base) else "domestic"
    if it.get("buffer_min") is not None:
        return it["buffer_min"], False, kind
    return BUFFER_DEFAULT[kind], True, kind


# ---------------- 대화 맥락 병합 (F3010) ----------------
def merge_context(new, prev, message=""):
    """후속 요청이면 직전 조건을 이어받고, 이번에 말한 값만 바꾼다. 이어받은 키 목록을 함께 돌려준다."""
    if not prev:
        return new, []
    new = dict(new)
    inherited = []
    kind = new.get("intent")
    said = any(new.get(k) for k in ("airport", "destination", "origin", "date", "from_time", "to_time",
                                    "flight_no", "arrive_airport_at", "buffer_min"))
    if kind == "unknown" and prev.get("intent") not in (None, "unknown", "greeting") and (said or FOLLOWUP.search(message or "")):
        kind = prev["intent"]
        new["intent"] = kind
        inherited.append("intent")
    if kind == prev.get("intent"):
        if new.get("date") and new.get("date") != prev.get("date"):
            new["flexible_date"] = False            # 날짜를 콕 집어 바꾸면 '아무 날짜'는 풀린다
        for k in CARRY:
            if k == "flexible_date" and new.get("date"):
                continue
            if new.get(k) in (None, False, "") and prev.get(k) not in (None, False, ""):
                new[k] = prev[k]
                inherited.append(k)
    elif kind in ("congestion", "facilities", "passenger", "arrivals", "flight_detail") and not new.get("airport") \
            and prev.get("airport"):
        new["airport"] = prev["airport"]
        inherited.append("airport")
    if kind == "flight_detail" and not new.get("flight_no") and prev.get("flight_no"):
        new["flight_no"] = prev["flight_no"]
        inherited.append("flight_no")
    # 도착 편: 기준 공항과 출발지가 같은 공항이면 출발지는 잘못 이어받은 값이므로 버린다
    if kind == "arrivals" and new.get("origin") and new.get("airport"):
        from app import airports
        if new["airport"] in [c for c in airports.resolve_codes(new["origin"]) if c in airports.NAMES]:
            new["origin"] = None
            inherited = [k for k in inherited if k != "origin"]
    return new, inherited


# ---------------- 노드: 분석 ----------------
# 요청에서 뽑는 조건을 명세 2.2 의 세 묶음으로 나눈다 (F3005).
#   기준: 계획의 기준점(날짜·시각·출발 공항·편명)  선호: 고르는 방향(목적지·출발지·가장 빠른 편·시설 종류)
#   제약: 넘으면 안 되는 조건(이 시각 전까지·공항 도착 시각·준비 시간)
CONDITION_GROUPS = {
    "기준": ("date", "flexible_date", "from_time", "airport", "flight_no"),
    "선호": ("destination", "origin", "earliest", "keyword"),
    "제약": ("to_time", "arrive_airport_at", "buffer_min"),
}


def group_conditions(data):
    """조건을 기준·선호·제약으로 묶고, 말하지 않아 비워 둔 항목 이름을 함께 돌려준다 (임의로 채우지 않는다)."""
    groups, empty = {}, []
    for group, keys in CONDITION_GROUPS.items():
        groups[group] = {}
        for k in keys:
            v = data.get(k)
            if v in (None, False, ""):
                empty.append(k)
            else:
                groups[group][k] = v
    return groups, empty


def analyze(state, emit):
    prev = (state.get("memory") or {}).get("intent")
    it, source, note = extract(state["message"], prev)
    data, inherited = merge_context(it.model_dump(), prev, state["message"])
    shown = {k: v for k, v in data.items() if v not in (None, False, "") and k not in ("intent", "reason")}
    groups, empty = group_conditions(data)
    emit("intent_analyzed", source=source, intent=data["intent"], reason=data.get("reason"),
         conditions=shown, groups=groups, empty=empty, inherited=inherited, note=note)
    return {"intent": data, "intent_source": source, "inherited": inherited, "analysis_note": note}


# ---------------- 노드: 계획 ----------------
def _widen_options(it):
    opts = []
    if it.get("from_time") or it.get("to_time") or it.get("arrive_airport_at"):
        opts.append(("drop_time", 0))
    if not it.get("date") or it.get("flexible_date"):
        opts += [("next_day", 1), ("next_day", 2), ("next_day", 3)]
    return opts


def _applied(it, replans):
    chosen = _widen_options(it)[:replans]
    drop = any(k == "drop_time" for k, _ in chosen)
    shift = max([v for k, v in chosen if k == "next_day"], default=0)
    return shift, drop


def describe_widen(it, attempt):
    opts = _widen_options(it)
    if attempt - 1 >= len(opts):
        return None
    kind, value = opts[attempt - 1]
    return "시간 조건을 풀고 같은 날 전체를 다시 조회" if kind == "drop_time" else f"{value}일 뒤 날짜로 넓혀 다시 조회"


def _default_base(codes):
    if all(airports.is_domestic(c) for c in codes):
        return "GMP" if "GMP" not in codes else "CJU"
    return airports.ICN


def _support_step(base, date, today):
    if date == today and base in airports.CONGESTION_AIRPORTS:
        return {"tool": "get_airport_congestion", "args": {"airport": base}, "role": "support"}
    if base == airports.ICN and date in (today, _shift(today, 1)):
        return {"tool": "get_incheon_passenger_forecast",
                "args": {"selectdate": "0" if date == today else "1"}, "role": "support"}
    return None


def plan(state, emit):
    it = state["intent"]
    kind = it["intent"]
    replans = state.get("replans", 0)
    today = _today()
    message = state.get("message") or ""
    steps, missing = [], None
    assumptions = list(state.get("assumptions") or []) if replans else []
    ctx = {"kind": kind}

    def assume(text):
        if text not in assumptions:
            assumptions.append(text)

    if kind in ("departures", "arrivals"):
        dep = kind == "departures"
        other_text = it.get("destination") if dep else it.get("origin")
        other_codes = _codes(other_text) if other_text else []
        base = it.get("airport")
        if other_text and not other_codes:
            missing = f"'{other_text}' 공항을 찾지 못했습니다. 도시 이름이나 공항 코드(예: SDJ)로 다시 알려 주십시오."
        elif not base:
            if other_codes:
                base = _default_base(other_codes) if dep else airports.ICN
                assume(f"{'출발' if dep else '도착'} 공항을 말하지 않아 {airports.official(base)}({base}) 기준으로 찾았습니다.")
            elif dep:
                missing = ("어느 공항에서 출발하는지, 또는 어디로 가는지 알려 주십시오. "
                           "예: '김포 출발 시간표', '센다이 가는 가장 빠른 편'")
            else:
                base = airports.ICN
                assume("도착 공항을 말하지 않아 인천(ICN) 기준으로 찾았습니다.")
        if not missing and base in other_codes:
            missing = "출발 공항과 목적지가 같습니다. 다시 알려 주십시오."
        if not missing:
            shift, drop = _applied(it, replans)
            date = _shift(it.get("date") or today, shift)
            if not it.get("date"):
                assume("날짜를 말하지 않아 오늘부터 찾았습니다.")
            args = {"airport": base, "date": date}
            if not drop:
                if it.get("from_time"):
                    args["from_time"] = it["from_time"]
                if it.get("to_time"):
                    args["to_time"] = it["to_time"]
            if len(other_codes) == 1:
                args["destination" if dep else "origin"] = other_codes[0]
            steps.append({"tool": "search_departures" if dep else "search_arrivals", "args": args,
                          "role": "candidates"})
            support = _support_step(base, date, today)
            if support:
                steps.append(support)
            ctx.update(base=base, other_codes=other_codes, date=date, shift=shift, dropped_time=drop,
                       direction="departure" if dep else "arrival")

    elif kind == "flight_detail":
        no = it.get("flight_no")
        if not no:
            missing = "편명을 알려 주십시오. 예: KE123, OZ102"
        else:
            date = it.get("date") or today
            args = {"flight_no": no, "date": date}
            if it.get("airport"):
                args["airport"] = it["airport"]
            steps.append({"tool": "search_flight", "args": args, "role": "candidates"})
            ctx.update(base=it.get("airport"), date=date)

    elif kind in ("congestion", "passenger"):
        base = it.get("airport") or (airports.ICN if kind == "passenger" else None)
        date = it.get("date") or today
        if not base:
            missing = "혼잡도를 볼 공항을 알려 주십시오. 예: 인천, 김포, 제주, 김해, 청주, 대구, 광주"
        elif base == airports.ICN:
            if kind == "congestion":
                assume("인천은 혼잡도 API 가 없어 시간대별 예상 승객 수로 대신 보여 드립니다.")
            steps.append({"tool": "get_incheon_passenger_forecast",
                          "args": {"selectdate": "1" if date == _shift(today, 1) else "0"}, "role": "info"})
        else:
            if base in airports.CONGESTION_AIRPORTS and kind == "congestion":
                steps.append({"tool": "get_airport_congestion", "args": {"airport": base}, "role": "info"})
            else:
                assume(f"{airports.official(base)}은 실시간 혼잡도 API 가 없어 시간대별 이용 흐름으로 보여 드립니다."
                       if kind == "congestion" else "인천 외 공항은 시간대별 이용 흐름으로 보여 드립니다.")
            steps.append({"tool": "get_hourly_traffic", "args": {"airport": base, "date": date}, "role": "info"})
        ctx.update(base=base, date=date)

    elif kind == "facilities":
        base = it.get("airport") or airports.ICN
        if not it.get("airport"):
            assume("공항을 말하지 않아 인천국제공항 기준으로 찾았습니다.")
        if base == airports.ICN:
            args = {"keyword": it.get("keyword")}
            if "입국" in message:
                args["area"] = "A"
            elif "출국" in message:
                args["area"] = "D"
            steps.append({"tool": "get_incheon_facilities", "args": {k: v for k, v in args.items() if v},
                          "role": "info"})
        else:
            steps.append({"tool": "get_commercial_facilities",
                          "args": {k: v for k, v in {"airport": base, "keyword": it.get("keyword")}.items() if v},
                          "role": "info"})
        ctx.update(base=base)

    # 후보를 고른 뒤 탑승구 등을 더 확인하는 상세 조회는 미리 인자를 알 수 없어 '후속 단계'로 밝혀 둔다 (F3020 대조용)
    followups = ["get_flight_detail"] if kind in ("departures", "arrivals") and steps else []
    emit("planned", attempt=replans, steps=[{"tool": s["tool"], "args": s["args"]} for s in steps],
         followups=followups, missing=missing, assumptions=assumptions)
    return {"steps": steps, "missing": missing, "assumptions": assumptions, "ctx": ctx}


# ---------------- 노드: 실행 (F3007) ----------------
def _key(step):
    return step["tool"] + json.dumps(step["args"], sort_keys=True, ensure_ascii=False)


def execute(state, emit):
    cache = dict(state.get("cache") or {})
    results = []
    for s in state.get("steps") or []:
        key = _key(s)
        if key in cache:
            r = cache[key]
            emit("tool_reused", tool=s["tool"], args=s["args"])
        else:
            cancel.check(state.get("run_id"))       # 도구를 부르기 직전에도 멈춤 요청을 확인한다
            emit("tool_called", tool=s["tool"], args=s["args"], role=s.get("role"))
            t0 = time.perf_counter()
            r = call(s["tool"], **s["args"])
            data = r.get("data") if r["ok"] else None
            emit("tool_result", tool=s["tool"], ok=r["ok"], code=None if r["ok"] else r["error"]["code"],
                 elapsed_ms=int((time.perf_counter() - t0) * 1000),
                 count=data.get("count") if isinstance(data, dict) else None)
            if r["ok"]:
                cache[key] = r                       # 실패는 저장하지 않는다 (다시 부를 수 있게)
        results.append({"tool": s["tool"], "args": s["args"], "role": s.get("role"), **r})
    return {"results": results, "cache": cache}


# ---------------- 노드: 판단 (F3008 · F3009) ----------------
def evaluate_flights(data, it, ctx, now=None):
    """후보 항공편을 제약과 비교한다. 외부 API 로 확인할 수 없는 값(체크인 마감)은 지어내지 않고 준비 시간으로 역산한다."""
    dep = ctx.get("direction") == "departure"
    codes = ctx.get("other_codes") or []
    key = "destination_code" if dep else "origin_code"
    rows = [r for r in data.get("flights") or [] if not codes or r.get(key) in codes]
    now = now or now_kst().replace(tzinfo=None)
    date = ctx.get("date")
    estimated = bool(data.get("date_estimated"))
    if estimated and data.get("date"):
        date = data["date"]          # 같은 요일 운항표로 추정했으면 그 날짜의 시각끼리 비교한다
    criteria, notes = [], []
    threshold = None
    buffer = None
    if dep:
        buffer, default, kind = _buffer(it, codes, ctx.get("base"))
        if it.get("arrive_airport_at") and not ctx.get("shift") and not ctx.get("dropped_time"):
            hhmm = it["arrive_airport_at"]
            arrive = datetime.strptime(date, "%Y%m%d") + timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[2:]))
            threshold = arrive + timedelta(minutes=buffer)
            criteria.append(f"공항 도착 {arrive:%H:%M} + 준비 {buffer}분 = {threshold:%H:%M} 이후 출발하는 편")
            if default:
                notes.append(f"준비 시간은 {'국제선' if kind == 'international' else '국내선'} 기본값 {buffer}분으로 계산했습니다.")
        elif date == now.strftime("%Y%m%d") and not estimated:
            threshold = now
            criteria.append(f"지금({now:%H:%M}) 이후 출발하는 편")
    elif date == now.strftime("%Y%m%d") and not it.get("from_time") and not estimated:
        criteria.append(f"지금({now:%H:%M}) 이후 도착 예정인 편")
    if codes:
        criteria.append("목적지 " + ", ".join(f"{airports.official(c, airports.name_of(c))}({c})" for c in codes) if dep
                        else "출발지 " + ", ".join(f"{airports.official(c, airports.name_of(c))}({c})" for c in codes))
    if it.get("earliest"):
        criteria.append("그중 가장 이른 편")

    candidates, rejected = [], []
    for r in rows:
        when = _parse(r.get("estimated") or r.get("scheduled"))
        remark = r.get("remark") or ""
        brief = {"flight_no": r.get("flight_no"), "airline": r.get("airline"), "time": _hm(r.get("estimated") or r.get("scheduled"))}
        if when is None:
            # 판단에 필요한 시각이 비어 있으면 값을 지어내지 않고 판단하지 않은 채 제외한다 (F3003)
            rejected.append({**brief, "reason": f"{'출발' if dep else '도착'} 시각 정보 없음(판단하지 않음)"})
        elif re.search(r"결항|cancel", remark, re.IGNORECASE):
            rejected.append({**brief, "reason": "결항"})
        elif dep and threshold and when and when < threshold:
            rejected.append({**brief, "reason": "준비 시간 부족" if it.get("arrive_airport_at") else "이미 출발 시각이 지남"})
        elif dep and re.search(r"출발|이륙|departed", remark, re.IGNORECASE) and date == now.strftime("%Y%m%d") and not estimated:
            rejected.append({**brief, "reason": "이미 출발함"})
        else:
            candidates.append(r)
    candidates.sort(key=lambda r: r.get("estimated") or r.get("scheduled") or "")
    chosen = candidates[0] if candidates and (it.get("earliest") or codes) else None
    if estimated and data.get("note"):
        notes.insert(0, data["note"])
    return {"candidates": candidates, "rejected": rejected, "chosen": chosen, "criteria": criteria,
            "notes": notes, "buffer": buffer, "matched_rows": len(rows), "date": date, "estimated": estimated,
            "requested_date": data.get("requested_date") or ctx.get("date")}


def reject_reasons(rejected):
    counts = {}
    for x in rejected or []:
        counts[x["reason"]] = counts.get(x["reason"], 0) + 1
    return counts


def judge(state, emit):
    it = state["intent"]
    kind = it["intent"]
    ctx = state.get("ctx") or {}
    replans = state.get("replans", 0)
    log = list(state.get("replan_log") or [])
    results = state.get("results") or []
    primary = next((r for r in results if r.get("role") == "candidates"), None)

    def replan(reason):
        log.append({"attempt": replans + 1, "reason": reason})
        emit("replanned", attempt=replans + 1, reason=reason)
        return {"decision": "replan", "replans": replans + 1, "replan_log": log}

    if state.get("missing") or not results:
        emit("judged", decision="respond", reason="도구가 필요 없거나 조건이 모자라 바로 답함")
        return {"decision": "respond", "judgement": None}
    if primary is None:
        emit("judged", decision="respond", reason="정보 조회 요청이라 비교할 제약 없이 바로 답함")
        return {"decision": "respond", "judgement": None}

    if not primary["ok"]:
        code = primary["error"]["code"]
        if code not in FATAL and replans == 0:
            return replan(f"도구 실패({code}) → 같은 조건으로 한 번 더 시도")
        emit("judged", decision="respond", reason=f"도구 실패({code}), 다시 해도 같은 결과라 바로 안내")
        return {"decision": "respond", "judgement": {"failed": True, "code": code}}

    if kind in ("departures", "arrivals"):
        j = evaluate_flights(primary["data"], it, ctx)
        if not j["candidates"]:
            nxt = describe_widen(it, replans + 1)
            if nxt and replans < config.REPLAN_LIMIT:
                return replan(f"조건을 만족하는 편이 없음 → {nxt}")
            j["limit_reached"] = replans >= config.REPLAN_LIMIT
            emit("judged", decision="respond", candidates=0, rejected=len(j["rejected"]),
                 rejected_reasons=reject_reasons(j["rejected"]), criteria=j["criteria"],
                 reason=f"재계획 상한({config.REPLAN_LIMIT}회)에 도달" if j["limit_reached"] else "더 넓힐 조건이 없음")
            return {"decision": "respond", "judgement": j}
        decision = "enrich" if j["chosen"] else "respond"
        emit("judged", decision=decision, candidates=len(j["candidates"]), rejected=len(j["rejected"]),
             rejected_reasons=reject_reasons(j["rejected"]), criteria=j["criteria"],
             chosen=(j["chosen"] or {}).get("flight_no"), date_estimated=j["estimated"])
        return {"decision": decision, "judgement": j}

    if kind == "flight_detail":
        emit("judged", decision="respond", count=primary["data"]["count"],
             reason=None if primary["data"]["count"] else "편명을 찾지 못함 (공항 전체를 이미 찾아 재계획하지 않음)")
        return {"decision": "respond", "judgement": None}

    emit("judged", decision="respond")
    return {"decision": "respond", "judgement": None}


# ---------------- 노드: 상세 보강 (F3003 · F3007) ----------------
def enrich(state, emit):
    j = dict(state.get("judgement") or {})
    chosen = j.get("chosen")
    ctx = state.get("ctx") or {}
    if not chosen:
        return {}
    if chosen.get("provider") != "KAC" or chosen.get("gate"):
        emit("detail_skipped", flight_no=chosen.get("flight_no"),
             reason="후보 목록에 탑승구·터미널·상태 항목이 이미 있어 상세 조회를 생략")
        return {}
    args = {"airport": ctx["base"], "flight_no": chosen["flight_no"], "date": ctx["date"],
            "direction": ctx["direction"]}
    emit("tool_called", tool="get_flight_detail", args=args, role="detail")
    t0 = time.perf_counter()
    r = call("get_flight_detail", **args)
    emit("tool_result", tool="get_flight_detail", ok=r["ok"], code=None if r["ok"] else r["error"]["code"],
         elapsed_ms=int((time.perf_counter() - t0) * 1000), count=r["data"]["count"] if r["ok"] else None)
    if r["ok"] and r["data"]["rows"]:
        d = r["data"]["rows"][0]
        merged = dict(chosen)
        for k in ("gate", "terminal", "estimated", "remark"):
            if d.get(k):
                merged[k] = d[k]
        j["chosen"] = merged
        j["candidates"] = [merged] + [c for c in j.get("candidates", []) if c is not chosen]
        j["detail"] = {"ok": True, "source": r["data"]["source"]}
    else:
        j["detail"] = {"ok": False, "message": r["error"]["message"] if not r["ok"] else "상세 자료에 이 편이 없습니다."}
    results = list(state.get("results") or []) + [{"tool": "get_flight_detail", "args": args, "role": "detail", **r}]
    return {"judgement": j, "results": results}


# ---------------- 노드: 응답 (F3011) ----------------
def _flight_line(r):
    dep = r.get("direction") != "arrival"
    est = r.get("estimated")
    est_txt = f"(예상 {_hm(est)})" if est and est != r.get("scheduled") else ""
    other = r.get("destination_name") if dep else r.get("origin_name")
    code = r.get("destination_code") if dep else r.get("origin_code")
    other = airports.official(code, other) if code in airports.OFFICIAL else other
    extra = "".join([f" · 탑승구 {r['gate']}" if r.get("gate") else "", f" · {r['terminal']}" if r.get("terminal") else "",
                     f" · 공동운항 {', '.join(r['codeshares'])}" if r.get("codeshares") else ""])
    return (f"{_hm(r.get('scheduled'))}{est_txt} {r.get('airline') or ''} {r.get('flight_no') or ''} "
            f"{'→' if dep else '←'} {other or '-'}({code or '-'}) · {r.get('remark') or '예정'}{extra}")


def _support_text(results, when):
    """보조 정보(F3004): 공항 도착 권장 시각 무렵의 혼잡 정도."""
    for r in results:
        if r.get("role") != "support":
            continue
        if not r["ok"]:
            return f"보조 정보({SOURCES.get(r['tool'], r['tool'])})는 불러오지 못했습니다."
        d = r["data"]
        hour = when.hour if when else None
        if r["tool"] == "get_incheon_passenger_forecast":
            row = next((x for x in d["rows"] if x.get("hour") == hour), None)
            if row:
                ratio = row.get("ratio", {})
                parts = []
                for key, label in (("t1_departure", "T1 출국"), ("t2_departure", "T2 출국")):
                    pct = ratio.get(key)
                    parts.append(f"{label} {row[key]:,}명" + (f"(오늘 최대의 {int(pct * 100)}%)" if pct is not None else ""))
                return f"참고: {hour}시대 인천국제공항 예상 인원 " + ", ".join(parts) + "."
        elif r["tool"] == "get_airport_congestion":
            row = d["rows"][0] if d["rows"] else None
            if row:
                return (f"참고: {airports.official(d['airport'])} 국내선 지금({row.get('time') or '-'}) 혼잡 {row.get('level') or '정보 없음'}단계 "
                        "(실시간 값이라 공항 도착 시각의 혼잡과는 다를 수 있음).")
    return None


def _sources(results):
    names = []
    for r in results:
        if not r.get("ok"):
            continue
        d = r.get("data") or {}
        name = SOURCES.get(d.get("provider")) if isinstance(d, dict) and d.get("provider") else SOURCES.get(r["tool"])
        if r["tool"] == "get_flight_detail" and isinstance(d, dict):
            name = d.get("source") or name
        if name and name not in names:
            names.append(name)
    return names


def _answer_flights(state):
    it, ctx = state["intent"], state.get("ctx") or {}
    j = state.get("judgement") or {}
    dep = ctx.get("direction") == "departure"
    base = ctx.get("base")
    lines = []
    chosen = j.get("chosen")
    cands = j.get("candidates") or []
    if chosen:
        sch = chosen.get("estimated") or chosen.get("scheduled")
        other = chosen.get("destination_name") if dep else chosen.get("origin_name")
        code = chosen.get("destination_code") if dep else chosen.get("origin_code")
        lines.append(f"추천: {chosen.get('airline') or ''} {chosen.get('flight_no')} — "
                     f"{_fmt_ymd(ctx.get('date'))}{' (추정)' if j.get('estimated') else ''} {_hm(chosen.get('scheduled'))} "
                     + (f"{airports.official(base)}({base}) 출발 → {airports.official(code, other) or '-'}({code or '-'})" if dep
                        else f"{airports.official(code, other) or '-'}({code or '-'}) 출발 → {airports.official(base)}({base}) 도착"))
        status = [f"상태 {chosen.get('remark') or '예정'}"]
        if chosen.get("estimated") and chosen.get("estimated") != chosen.get("scheduled"):
            status.append(f"예상 {_hm(chosen['estimated'])}")
        status.append(f"탑승구 {chosen['gate']}" if chosen.get("gate") else "탑승구 미배정(출발 전 다시 확인)")
        if chosen.get("terminal"):
            status.append(f"터미널 {chosen['terminal']}")
        if chosen.get("codeshares"):
            status.append(f"공동운항 {', '.join(chosen['codeshares'])} (같은 비행기)")
        lines.append("- " + " · ".join(status))
        when = _parse(sch)
        if dep and when and j.get("buffer"):
            # 체크인 마감·공항 도착 시각은 공공데이터로 확인할 수 없는 값이라 지어내지 않고 출발 시각에서 역산한다 (F3008)
            line = checkin.line_of(base, chosen.get("destination_code"))
            close = checkin.deadline(when.strftime(FMT), base, line)
            latest = when - timedelta(minutes=j["buffer"])
            lines.append(f"- 늦어도 공항 도착 {latest:%H:%M} (출발 {when:%H:%M}에서 짐·보안검색 여유 {j['buffer']}분을 뺀 역산)")
            lines.append(f"- 체크인 마감(추정) {close[11:]} ({'국제선' if line == 'international' else '국내선'} 출발 "
                         f"{checkin.minutes(base, line)}분 전, 주요 항공사 기준 — 공공데이터에 없는 값이라 항공사 안내로 확인)")
            support = _support_text(state.get("results") or [], latest)
            if support:
                lines.append("- " + support)
        others = [c for c in cands if c is not chosen and c.get("flight_no") != chosen.get("flight_no")][:3]
        if others:
            lines.append("다른 후보:")
            lines += [f"- {_flight_line(c)}" for c in others]
    elif cands:
        lines.append(f"{airports.official(base)}({base}) {_fmt_ymd(ctx.get('date'))} {'출발' if dep else '도착'} "
                     f"{len(cands)}편 (이른 순)")
        lines += [f"- {_flight_line(c)}" for c in cands[:10]]
        if len(cands) > 10:
            lines.append(f"(처음 10편만 표시, '{'출발' if dep else '도착'} 시간표' 화면에서 전체를 볼 수 있습니다)")
    else:
        searched = ctx.get("date")
        lines.append(f"조건을 만족하는 항공편을 찾지 못했습니다 (마지막으로 찾은 날짜 {_fmt_ymd(searched)}).")
        if j.get("limit_reached"):
            lines.append(f"재계획 상한({config.REPLAN_LIMIT}회)에 도달해 멈췄습니다.")
        lines.append("조건을 바꿔 보십시오: 다른 출발 공항(예: 김포·김해), 다른 날짜, 또는 시간 조건 완화.")
    if j.get("criteria"):
        lines.append("판단 기준: " + " · ".join(j["criteria"]))
    if j.get("rejected"):
        # 제외한 후보는 사유와 편명을 함께 밝힌다 (F3011). 사유마다 앞의 3편까지 적는다.
        parts = []
        for reason, n in reject_reasons(j["rejected"]).items():
            some = [x for x in j["rejected"] if x["reason"] == reason][:3]
            names = ", ".join(f"{x.get('flight_no') or '-'} {x.get('time') or '-'}" for x in some)
            parts.append(f"{reason} {n}편({names}{' 외' if n > len(some) else ''})")
        lines.append("제외한 후보: " + "; ".join(parts))
    for n in j.get("notes") or []:
        lines.append(n)
    if j.get("detail") and not j["detail"].get("ok"):
        lines.append(f"상세 조회: {j['detail']['message']}")
    return lines


def _answer_info(state):
    lines = []
    for r in state.get("results") or []:
        if r.get("role") not in ("candidates", "info"):
            continue
        if not r["ok"]:
            msg = r["error"]["message"]
            lines.append(f"조회 실패: {msg}")
            if r["error"]["code"] == "NO_KEY":
                lines.append("설정 안내: 프로젝트 폴더의 .env 에 DATA_GO_KR_KEY 를 넣고 서버를 다시 켜십시오.")
            continue
        d, tool = r["data"], r["tool"]
        if tool == "search_flight":
            lines.append(f"{airports.official(d['airport'])}({d['airport']}) {_fmt_ymd(d['date'])} {d['flight_no']} 조회 결과 {d['count']}건")
            lines += [f"- {'출발' if f.get('direction') == 'departure' else '도착'} {_flight_line(f)}" for f in d["flights"][:5]]
            if d.get("note"):
                lines.append(d["note"])
        elif tool == "get_incheon_passenger_forecast":
            hourly = [x for x in d["rows"] if x.get("hour") is not None]
            now_row = next((x for x in hourly if x["hour"] == d.get("current_hour")), None)
            lines.append(f"인천국제공항 {'오늘' if d['selectdate'] == '0' else '내일'} 시간대별 예상 승객 (참고용)")
            if now_row:
                ratio = now_row.get("ratio") or {}
                lines.append(f"- 지금({now_row['hour']}시대): T1 출국 {now_row['t1_departure']:,}명, T2 출국 {now_row['t2_departure']:,}명 "
                             f"(T1 출국은 오늘 최대의 {int((ratio.get('t1_departure') or 0) * 100)}%)")
            top = sorted(hourly, key=lambda x: x["t1_departure"] + x["t2_departure"], reverse=True)[:3]
            if top:
                lines.append("- 출국이 가장 몰리는 시간: " + ", ".join(f"{x['hour']}시대({x['t1_departure'] + x['t2_departure']:,}명)" for x in top))
            lines.append(d["note"])
        elif tool == "get_airport_congestion":
            row = d["rows"][0] if d["rows"] else None
            if row:
                lines.append(f"{airports.official(d['airport'])} 국내선 지금 혼잡 ({row.get('time') or '-'} 기준, 제공기관 단계)")
                lines.append(f"- 전체 {row.get('level') or '-'}단계")
                for seg in d.get("segments") or []:
                    if row.get(seg["key"]) is not None:
                        lines.append(f"- {seg['label']}: {row[seg['key']]}단계")
            else:
                lines.append(f"{airports.official(d['airport'])}: 지금 제공되는 실시간 혼잡도가 없습니다.")
        elif tool == "get_hourly_traffic":
            unit = d["unit"]
            busy = sorted(d["rows"], key=lambda x: x["value"], reverse=True)[:3]
            now_h = now_kst().hour
            now_row = next((x for x in d["rows"] if x["hour"] == now_h), None)
            label = "예상 탑승객" if d["source"] == "expected_passengers" else "출발·도착 편수"
            lines.append(f"{airports.official(d['airport'])} {_fmt_ymd(d['date'])} 시간대별 {label}")
            if now_row and d["date"] == _today():
                lines.append(f"- 지금({now_h}시대): {now_row['value']:,}{unit}")
            if busy and busy[0]["value"]:
                lines.append("- 가장 붐비는 시간: " + ", ".join(f"{x['hour']}시대 {x['value']:,}{unit}" for x in busy if x["value"]))
            quiet = [x for x in d["rows"] if 6 <= x["hour"] <= 21]
            if quiet:
                q = min(quiet, key=lambda x: x["value"])
                lines.append(f"- 비교적 한산한 낮 시간: {q['hour']}시대 ({q['value']:,}{unit})")
            lines.append(d["note"])
        elif tool == "get_incheon_facilities":
            kw = r["args"].get("keyword")
            lines.append(f"인천국제공항 {kw + ' 관련 ' if kw else ''}시설 {d['count']}곳" + (f" (처음 8곳)" if d["count"] > 8 else ""))
            for x in d["rows"][:8]:
                bits = [x.get("location") or "위치 정보 없음", x.get("area_name"), x.get("hours")]
                lines.append(f"- {x.get('name') or '-'}: " + " · ".join(b for b in bits if b))
            if d["count"] > 8:
                lines.append("'공항 시설' 화면에서 층별로 모두 볼 수 있습니다.")
            if d.get("empty"):
                lines.append(d["note"])
        elif tool == "get_commercial_facilities":
            kw = r["args"].get("keyword")
            lines.append(f"{airports.official(d['airport'])} {kw + ' 관련 ' if kw else ''}매장 {d['count']}곳" + (" (처음 8곳)" if d["count"] > 8 else ""))
            for x in d["rows"][:8]:
                lines.append(f"- {x.get('name') or '-'} ({x.get('business_type') or x.get('category')}): {x.get('location') or '위치 정보 없음'}")
            if d["count"] > 8:
                lines.append("'공항 시설' 화면에서 구역·층별로 모두 볼 수 있습니다.")
            lines.append(d["note"])
    return lines


def respond(state, emit):
    it = state.get("intent") or {}
    kind = it.get("intent")
    cards = None
    summary = ""
    j = state.get("judgement") or {}
    if state.get("missing"):
        lines = [state["missing"]]
    elif kind == "greeting":
        lines = [GREETING]
    elif kind == "plan":
        lines = [PLAN_HELP]
    elif kind == "unknown" or not state.get("results"):
        lines = [HELP]
    elif kind in ("departures", "arrivals") and not j.get("failed"):
        lines = _answer_flights(state)
        ctx = state.get("ctx") or {}
        flights = ([j["chosen"]] if j.get("chosen") else []) + \
                  [c for c in (j.get("candidates") or []) if not j.get("chosen") or c.get("flight_no") != j["chosen"].get("flight_no")]
        if flights:
            cards = {"airport": ctx.get("base"), "airport_name": airports.official(ctx.get("base")),
                     "date": ctx.get("date"), "chosen": (j.get("chosen") or {}).get("flight_no"),
                     "flights": flights[:5]}
        summary = (f"{kind} {ctx.get('base')}→{','.join(ctx.get('other_codes') or []) or '전체'} {ctx.get('date')} "
                   f"추천 {(j.get('chosen') or {}).get('flight_no') or '없음'}")
    else:
        lines = _answer_info(state)
        primary = next((r for r in state.get("results") or [] if r.get("role") == "candidates" and r["ok"]), None)
        if kind == "flight_detail" and primary and primary["data"]["flights"]:
            d = primary["data"]
            cards = {"airport": d["airport"], "airport_name": airports.official(d["airport"]), "date": d["date"],
                     "chosen": d["flight_no"], "flights": d["flights"][:5]}
        summary = f"{kind} {(state.get('ctx') or {}).get('base') or ''}"
    log = state.get("replan_log") or []
    if log:
        lines.append(f"재계획 {len(log)}회: " + " / ".join(x["reason"] for x in log))
    for a in state.get("assumptions") or []:
        lines.append("가정: " + a)
    if state.get("analysis_note"):
        lines.append("참고: " + state["analysis_note"])
    src = _sources(state.get("results") or [])
    if src:
        lines.append("근거 자료: " + ", ".join(src))
    if kind in ("departures", "arrivals", "flight_detail") and state.get("results"):
        lines.append("운항 정보는 실시간으로 바뀝니다. 출발 전에 항공사 안내를 다시 확인하십시오.")
    text = "\n".join(l for l in lines if l)
    emit("responded", chars=len(text), cards=len(cards["flights"]) if cards else 0,
         replans=state.get("replans", 0))
    return {"answer": text, "cards": cards, "summary": summary or kind or ""}
