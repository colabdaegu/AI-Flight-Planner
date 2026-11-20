"""진행 중인 대화 실행 멈추기.

화면에서 '새 대화'를 누르면 진행 중인 실행의 run_id 로 멈춤을 요청한다.
메인 에이전트는 단계(노드)를 시작할 때와 도구를 부르기 직전에 확인하고, 멈춤이 요청됐으면 거기서 그친다.
이미 보낸 외부 API 요청 하나는 끝까지 기다린 뒤 멈춘다.
"""
import threading

_lock = threading.Lock()
_flags = set()
MAX_FLAGS = 1000          # 시작하지 않은 실행의 멈춤 요청이 쌓이지 않게 상한을 둔다


class Cancelled(Exception):
    """사용자가 실행을 멈췄다."""


def request(run_id):
    if not run_id:
        return
    with _lock:
        if len(_flags) >= MAX_FLAGS:
            _flags.clear()
        _flags.add(run_id)


def is_cancelled(run_id):
    if not run_id:
        return False
    with _lock:
        return run_id in _flags


def check(run_id):
    if is_cancelled(run_id):
        raise Cancelled(run_id)


def done(run_id):
    with _lock:
        _flags.discard(run_id)
