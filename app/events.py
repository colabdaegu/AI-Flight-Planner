"""실행 이벤트 발행과 구독 (F3012).

에이전트는 emit 만 한다. 누가 듣는지 모른다. 구독자가 없어도, 구독자가 고장 나도 대화는 계속된다.
"""
from datetime import datetime


class EventBus:
    def __init__(self):
        self._subs = []

    def subscribe(self, fn):
        self._subs.append(fn)

    def publish(self, event, session_id, **payload):
        msg = {"event": event, "session_id": session_id,
               "ts": datetime.now().isoformat(timespec="seconds"), "payload": payload}
        for fn in list(self._subs):
            try:
                fn(msg)
            except Exception:
                pass
        return msg


bus = EventBus()


def make_emitter(session_id, trace):
    """세션 번호를 붙여 발행하고, 같은 메시지를 응답용 trace 에도 쌓는다."""
    def emit(event, **payload):
        msg = bus.publish(event, session_id, **payload)
        trace.append(msg)
        return msg
    return emit
