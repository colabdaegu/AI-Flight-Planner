"""실행 이벤트 발행과 구독 (F3012).

에이전트는 emit 만 한다. 누가 듣는지 모른다. 구독자가 없어도, 구독자가 고장 나도 대화는 계속된다.
"""
from datetime import datetime


class EventBus:
    def __init__(self):
        self._subs = []
        self._reported = set()

    def subscribe(self, fn):
        self._subs.append(fn)

    def unsubscribe(self, fn):
        """구독을 끊는다 (없으면 아무 일도 하지 않음). 로깅 Agent 없이도 메인 Agent 가 도는지 확인할 때 쓴다."""
        if fn in self._subs:
            self._subs.remove(fn)

    @property
    def subscriber_count(self):
        return len(self._subs)

    def publish(self, event, session_id, **payload):
        msg = {"event": event, "session_id": session_id,
               "ts": datetime.now().isoformat(timespec="seconds"), "payload": payload}
        for fn in list(self._subs):
            try:
                fn(msg)
            except Exception as e:          # 구독자(로그 저장)가 실패해도 발행·대화는 계속된다 (F3013)
                if id(fn) not in self._reported:        # 같은 구독자의 실패는 한 번만 알린다
                    self._reported.add(id(fn))
                    print("[event-subscriber] 구독자 실패 — 대화는 계속합니다:", type(e).__name__)
        return msg


bus = EventBus()


def make_emitter(session_id, trace):
    """세션 번호를 붙여 발행하고, 같은 메시지를 응답용 trace 에도 쌓는다."""
    def emit(event, **payload):
        msg = bus.publish(event, session_id, **payload)
        trace.append(msg)
        return msg
    return emit
