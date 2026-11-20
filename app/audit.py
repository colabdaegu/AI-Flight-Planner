"""로깅 에이전트의 이상 징후 판단 (심화 F3020).

판정은 규칙으로만 한다 (LLM 없음). 실행 로그의 이벤트를 대조하고 횟수를 센다.
  - REPEATED_CALL   : 같은 도구를 같은 입력으로 다시 부름.
                      성공한 결과가 있는데 또 부르거나, 실패 뒤 재시도(설계상 1회)를 넘겨 부르면 이상.
  - EXCESSIVE_REPLAN: 재계획 횟수가 상한(config.REPLAN_LIMIT)에 이름.
  - UNPLANNED_CALL  : 직전 'planned' 이벤트의 작업 목록에도, 후속 단계(followups)에도 없는 도구를 부름.
근거가 되는 이벤트가 없으면 '이상 없음'이 아니라 '판정 불가'(undetermined)로 남긴다.
  - 실행의 시작(request_received)이나 끝(responded)이 없음
  - 도구를 불렀는데 그 앞에 계획 이벤트(planned)가 없음

이상(anomaly)으로 판정한 실행에만 LLM 으로 원인을 진단한다 (diagnose). 정상·판정 불가에는 부르지 않는다.
진단은 판정과 따로 저장하고 경고 수에 섞지 않는다. 진단이 실패해도 판정과 실행 로그는 그대로 남는다.
"""
import json
import re
from datetime import datetime

from app import config

CODES = {
    "REPEATED_CALL": "같은 도구 같은 입력 반복 호출",
    "EXCESSIVE_REPLAN": "재계획 과다 반복",
    "UNPLANNED_CALL": "계획에 없던 도구 호출",
}
RETRY_AFTER_FAILURE = 1        # 실패 뒤 같은 입력으로 다시 부르는 것은 1번까지 설계에 있다 (judge 노드)


def _key(tool, args):
    return f"{tool} {json.dumps(args or {}, sort_keys=True, ensure_ascii=False)}"


def split_runs(events):
    """세션 이벤트를 실행(질문 1건) 단위로 나눈다. 첫 request_received 앞의 이벤트는 앞 실행이 없으므로 따로 묶는다."""
    runs, cur = [], []
    for e in events or []:
        if e["event"] == "request_received" and cur:
            runs.append(cur)
            cur = []
        cur.append(e)
    if cur:
        runs.append(cur)
    return runs


def judge_run(events, run_index=0, limit=None):
    """한 실행의 이벤트 목록으로 판정한다. 반환: {run_index, question, started, status, warnings, checks}."""
    limit = config.REPLAN_LIMIT if limit is None else limit
    warnings, undetermined = [], []

    def warn(code, message, evidence):
        warnings.append({"code": code, "label": CODES[code], "message": message, "evidence": evidence})

    def ev(i):
        e = events[i]
        return {"seq": i + 1, "event": e["event"], "ts": e.get("ts")}

    start = events[0] if events and events[0]["event"] == "request_received" else None
    finished = any(e["event"] == "responded" for e in events)
    if not start:
        undetermined.append("실행 시작 이벤트(request_received)가 없습니다.")
    if not finished:
        undetermined.append("응답 이벤트(responded)가 없어 실행이 끝났는지 알 수 없습니다.")

    # 1) 같은 도구 같은 입력 반복
    calls = {}                 # key -> [(index, ok)]
    open_call = {}             # tool -> (key, index)  결과를 기다리는 호출
    for i, e in enumerate(events):
        p = e.get("payload") or {}
        if e["event"] == "tool_called":
            k = _key(p.get("tool"), p.get("args"))
            calls.setdefault(k, []).append([i, None])
            open_call[p.get("tool")] = (k, len(calls[k]) - 1)
        elif e["event"] == "tool_result" and p.get("tool") in open_call:
            k, n = open_call.pop(p["tool"])
            calls[k][n][1] = bool(p.get("ok"))
    repeated = 0
    for k, items in calls.items():
        succeeded, failures = False, 0
        for n, (i, ok) in enumerate(items):
            if n and (succeeded or failures > RETRY_AFTER_FAILURE):
                why = "이미 성공한 결과가 있는데 같은 입력으로 다시 불렀습니다" if succeeded \
                    else f"실패 뒤 재시도 {RETRY_AFTER_FAILURE}회를 넘겨 같은 입력으로 다시 불렀습니다"
                warn("REPEATED_CALL", f"{k} — {why} ({n + 1}번째 호출).", [ev(j) for j, _ in items[:n + 1]])
                repeated += 1
                break
            if ok:
                succeeded = True
            elif ok is False:
                failures += 1

    # 2) 재계획 과다
    replans = [i for i, e in enumerate(events) if e["event"] == "replanned"]
    if len(replans) >= limit:
        warn("EXCESSIVE_REPLAN", f"재계획 {len(replans)}회로 상한 {limit}회에 이르렀습니다.", [ev(i) for i in replans])

    # 3) 계획에 없던 호출
    plan, unplanned, no_plan = None, 0, False
    for i, e in enumerate(events):
        p = e.get("payload") or {}
        if e["event"] == "planned":
            plan = {"steps": {_key(s.get("tool"), s.get("args")) for s in p.get("steps") or []},
                    "followups": set(p.get("followups") or []), "index": i}
        elif e["event"] == "tool_called":
            if plan is None:
                no_plan = True
                continue
            if _key(p.get("tool"), p.get("args")) not in plan["steps"] and p.get("tool") not in plan["followups"]:
                warn("UNPLANNED_CALL", f"{p.get('tool')} 호출이 직전 계획의 작업 목록에 없습니다.",
                     [ev(plan["index"]), ev(i)])
                unplanned += 1
    if no_plan:
        undetermined.append("도구를 불렀지만 그 앞에 계획 이벤트(planned)가 없어 계획과 대조할 수 없습니다.")
    elif plan is None and finished:
        undetermined.append("계획 이벤트(planned)가 없어 계획과 대조할 수 없습니다.")

    checks = {
        "repeated_call": {"calls": sum(len(v) for v in calls.values()), "distinct": len(calls), "flagged": repeated},
        "excessive_replan": {"replans": len(replans), "limit": limit, "flagged": int(len(replans) >= limit)},
        "unplanned_call": {"flagged": unplanned, "judged": plan is not None and not no_plan},
    }
    status = "anomaly" if warnings else ("undetermined" if undetermined else "normal")
    payload = (start or {}).get("payload") or {}
    return {"run_index": run_index, "question": payload.get("message"), "started": (start or {}).get("ts"),
            "status": status, "warnings": warnings, "checks": checks, "undetermined": undetermined}


def judge_session(events):
    return [judge_run(run, i) for i, run in enumerate(split_runs(events))]


# ---------------- 원인 진단 (이상으로 판정한 실행에만) ----------------
def _compact(events, limit=60):
    out = []
    for i, e in enumerate(events[:limit]):
        p = json.dumps(e.get("payload") or {}, ensure_ascii=False)
        out.append(f"{i + 1}. {e['event']} {p[:220]}")
    return "\n".join(out)


def _three_sentences(text):
    parts = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text.strip()) if s.strip()]
    return " ".join(parts[:3])


def _llm(prompt_system, prompt_user):
    from langchain_openai import ChatOpenAI          # 지연 import

    llm = ChatOpenAI(model=config.GMS_MODEL, api_key=config.GMS_API_KEY, base_url=config.GMS_BASE_URL,
                     timeout=20, max_retries=1)
    return llm.invoke([("system", prompt_system), ("user", prompt_user)]).content


def diagnose(events, verdict, llm=None):
    """이상 판정을 받은 실행의 원인을 LLM 으로 세 문장 이내로 진단한다. 실패해도 예외를 밖으로 내지 않는다."""
    now = datetime.now().isoformat(timespec="seconds")
    if verdict.get("status") != "anomaly":
        return None                                       # 정상·판정 불가에는 LLM 을 부르지 않는다
    if llm is None and not config.GMS_API_KEY:
        return {"status": "skipped", "reason": "GMS_API_KEY 가 없어 원인 진단을 건너뛰었습니다.", "at": now}
    system = ("너는 여행 일정 플래너 에이전트의 실행 기록을 읽고 이상 원인을 진단한다. 한국어로 세 문장 이내로 답한다. "
              "첫 문장: 어느 쪽 문제인지(메인 Agent의 계획·실행·판단 단계, 도구·외부 API, 로깅 중 하나). "
              "둘째 문장: 근거가 된 이벤트 번호. 셋째 문장: 무엇을 고치면 되는지. 추측은 추측이라고 밝힌다.")
    user = ("판정 결과(규칙):\n" + json.dumps(verdict["warnings"], ensure_ascii=False)
            + "\n\n실행 기록(번호. 이벤트 내용):\n" + _compact(events))
    try:
        text = (llm or _llm)(system, user)
        return {"status": "done", "text": _three_sentences(str(text)), "model": config.GMS_MODEL, "at": now}
    except Exception as e:                                # 진단 실패는 판정과 로그에 영향을 주지 않는다
        return {"status": "failed", "reason": f"LLM 호출 실패({type(e).__name__})", "at": now}
