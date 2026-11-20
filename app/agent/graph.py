"""LangGraph 조립 (명세 F3005 ~ F3012).

analyze → plan → execute → judge ─┬─ replan  → plan
                                  ├─ enrich  → respond
                                  └─ respond → END
- 각 노드의 진입·종료와 소요 시간을 이벤트로 발행한다 (F3012). 발행은 구독자(로깅 에이전트)에 의존하지 않는다.
- langgraph 는 실행 시점에만 import 한다. 설치돼 있지 않으면 같은 순서로 직접 실행하고 그 사실을 이벤트로 남긴다.
"""
import time
from typing import Optional, TypedDict

from app.agent import cancel, nodes
from app.agent.memory import memory
from app.events import make_emitter
from app.store.audits import audit_store


class AgentState(TypedDict, total=False):
    message: str
    session_id: str
    memory: Optional[dict]
    intent: dict
    intent_source: str
    inherited: list
    analysis_note: Optional[str]
    steps: list
    missing: Optional[str]
    assumptions: list
    ctx: dict
    results: list
    cache: dict
    replans: int
    replan_log: list
    prev_audit: Optional[dict]
    run_id: Optional[str]
    decision: str
    judgement: Optional[dict]
    answer: str
    cards: Optional[dict]
    summary: str


NODES = (("analyze", nodes.analyze), ("plan", nodes.plan), ("execute", nodes.execute),
         ("judge", nodes.judge), ("enrich", nodes.enrich), ("respond", nodes.respond))
NEXT = {"analyze": "plan", "plan": "execute", "execute": "judge", "enrich": "respond", "respond": None}
# 설계 문서(docs/AGENT.md 2장)의 실행 단계 = 그래프 노드. 명세 4.2: 분석 → 계획 → 실행 → 판단 → 응답
STAGES = tuple(name for name, _ in NODES)
STAGE_LABELS = {"analyze": "의도 분석", "plan": "계획 수립", "execute": "Tool 호출", "judge": "결과 통합·제약 판단",
                "enrich": "상세 보강", "respond": "최종 응답"}


def _step(name, fn, emit):
    def run(state):
        cancel.check(state.get("run_id"))            # '새 대화'로 멈춤을 요청했으면 다음 단계로 가지 않는다
        emit("step_started", step=name)
        t0 = time.perf_counter()
        out = fn(state, emit) or {}
        emit("step_finished", step=name, elapsed_ms=int((time.perf_counter() - t0) * 1000))
        return out
    return run


def build_graph(emit):
    from langgraph.graph import END, StateGraph

    g = StateGraph(AgentState)
    for name, fn in NODES:
        g.add_node(name, _step(name, fn, emit))
    g.set_entry_point("analyze")
    g.add_edge("analyze", "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "judge")
    g.add_conditional_edges("judge", lambda s: s["decision"],
                            {"replan": "plan", "enrich": "enrich", "respond": "respond"})
    g.add_edge("enrich", "respond")
    g.add_edge("respond", END)
    return g.compile()


def structure():
    """그래프 구조를 Mermaid 글로 돌려준다 (명세 4.2 '그래프 구조 출력'). langgraph 가 있으면 컴파일한 그래프에서 뽑는다."""
    try:
        text = build_graph(lambda *a, **k: None).get_graph().draw_mermaid()
    except Exception:                                # langgraph 가 없거나 그리기를 못 하면 같은 구조를 직접 적는다
        lines = ["graph TD;", "    __start__([__start__]) --> analyze;"]
        lines += [f"    {a} --> {b};" for a, b in (("analyze", "plan"), ("plan", "execute"), ("execute", "judge"),
                                                     ("enrich", "respond"))]
        lines += ["    judge -. replan .-> plan;", "    judge -. enrich .-> enrich;", "    judge -. respond .-> respond;",
                  "    respond --> __end__([__end__]);"]
        text = "\n".join(lines)
    labels = "\n".join(f"    %% {k}: {v}" for k, v in STAGE_LABELS.items())
    return text.rstrip() + "\n" + labels


def run_plain(state, emit):
    """langgraph 가 없을 때 같은 노드를 같은 순서로 실행한다."""
    funcs = dict(NODES)
    name = "analyze"
    for _ in range(40):
        state = {**state, **_step(name, funcs[name], emit)(state)}
        name = {"replan": "plan", "enrich": "enrich", "respond": "respond"}[state["decision"]] \
            if name == "judge" else NEXT[name]
        if name is None:
            break
    return state


def previous_audit(session_id):
    """직전 실행의 판정 (없거나 읽지 못하면 None). 판정 저장소가 고장 나도 대화는 계속된다."""
    try:
        return audit_store.latest(session_id)
    except Exception:
        return None


def run_agent(message, session_id, trace, run_id=None):
    emit = make_emitter(session_id, trace)
    mem = memory.get(session_id)
    emit("request_received", message=message[:200], chars=len(message), has_memory=bool(mem))
    prev = previous_audit(session_id)
    if prev:
        # 로깅 에이전트가 남긴 직전 실행 판정을 참조한다 (F3020). 경고 종류를 이번 실행 상태에 싣는다.
        emit("audit_referenced", run_index=prev["run_index"], status=prev["status"],
             warnings=[w["code"] for w in prev["warnings"]])
    init = {"message": message, "session_id": session_id, "memory": mem, "replans": 0, "cache": {},
            "replan_log": [], "assumptions": [], "prev_audit": prev, "run_id": run_id}
    try:
        try:
            graph = build_graph(emit)
        except ImportError:
            emit("engine_fallback", reason="langgraph 가 설치돼 있지 않아 같은 순서로 직접 실행")
            final = run_plain(init, emit)
        else:
            final = graph.invoke(init, {"recursion_limit": 40})
    except cancel.Cancelled:
        # 멈춘 실행은 대화 맥락에 남기지 않는다. 실행 로그에는 멈춘 사실을 남긴다.
        emit("cancelled", reason="사용자가 새 대화를 시작해 진행 중이던 실행을 멈춤")
        return {"cancelled": True, "answer": None, "cards": None, "intent": None, "intent_source": None, "replans": 0}
    finally:
        cancel.done(run_id)
    intent = dict(final.get("intent") or {})
    ctx = final.get("ctx") or {}
    chosen = ((final.get("judgement") or {}).get("chosen") or {}).get("flight_no")
    if ctx.get("base"):
        intent["airport"] = ctx["base"]                  # 기본값으로 정한 공항도 다음 요청에서 이어 쓴다
    if chosen:
        intent["flight_no"] = chosen                     # '그 편 탑승구는?' 같은 후속 질문용
    memory.remember(session_id, intent, final.get("summary") or "", message)
    emit("memory_updated", intent=intent.get("intent"), summary=final.get("summary"))
    return {"answer": final.get("answer") or nodes.HELP, "cards": final.get("cards"),
            "intent": intent.get("intent"), "intent_source": final.get("intent_source"),
            "replans": final.get("replans", 0)}


if __name__ == "__main__":          # python -m app.agent.graph  → 그래프 구조(Mermaid) 출력
    print(structure())
