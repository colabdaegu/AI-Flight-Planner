"""체크인 마감 계산 (명세 제약식).

마감(arrive_by) = (예상 출발, 없으면 계획 출발) - 체크인 소요시간(분)
조건: 사용자 도착 예정 시각 <= 마감
혼잡도·승객 예고는 여기서 쓰지 않는다 (정보 제공용).
"""
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M"


def parse(value):
    return datetime.strptime(value, FMT)


def arrive_by(scheduled, estimated, buffer_min):
    if buffer_min is None or int(buffer_min) < 0:
        raise ValueError("체크인 소요시간(분)이 필요합니다.")
    base = parse(estimated or scheduled)
    return (base - timedelta(minutes=int(buffer_min))).strftime(FMT)


def overlaps(a_start, a_end, b_start, b_end):
    """두 준비 구간 [시작, 출발] 이 겹치는지. 맞닿는 경계는 겹치지 않는 것으로 본다."""
    return parse(a_start) < parse(b_end) and parse(b_start) < parse(a_end)
