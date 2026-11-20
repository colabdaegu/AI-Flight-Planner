"""인천공항 출입국 승객 예고 (인천국제공항공사 passgrAnncmt). 시간대별 예상 인원, 정보 제공용.

필드 이름(t1dgsum1, t1egsum2 …)에서 터미널(t1·t2)과 출국(dg)·입국(eg)을 읽어 시간대별 합계를 만든다.
공사가 정한 '혼잡' 기준값은 API 에 없으므로, 하루 중 가장 많은 시간대 대비 비율로만 상대 비교한다.
"""
import re
from datetime import datetime

from app.tools.common import fetch, pick
from app.tools.flights import KST
from app.tools.registry import Tool, register

URL = "http://apis.data.go.kr/B551177/passgrAnncmt/getPassgrAnncmt"   # 명세대로 HTTP
FIELD = re.compile(r"^t([12])(dg|eg)sum\d+$", re.IGNORECASE)
KEYS = ("t1_departure", "t1_arrival", "t2_departure", "t2_arrival")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hour(label):
    m = re.match(r"^(\d{1,2})\D", str(label or ""))
    return int(m.group(1)) if m else None


def get_incheon_passenger_forecast(selectdate="0"):
    if selectdate not in ("0", "1"):
        raise ValueError("selectdate 는 0(오늘) 또는 1(내일)이어야 합니다.")
    parsed = fetch(URL, {"selectdate": selectdate, "numOfRows": 50, "pageNo": 1})
    rows = []
    for i in parsed["items"]:
        sums = dict.fromkeys(KEYS, 0.0)
        for k, v in i.items():
            m = FIELD.match(k)
            n = _num(v)
            if m and n is not None:
                kind = "departure" if m.group(2).lower() == "dg" else "arrival"
                sums[f"t{m.group(1)}_{kind}"] += n
        label = pick(i, "atime")
        rows.append({"date": pick(i, "adate"), "time": label, "hour": _hour(label),
                     **{k: round(v) for k, v in sums.items()},
                     "values": {k: v for k, v in i.items() if k.lower() not in ("adate", "atime")}})
    hourly = [r for r in rows if r["hour"] is not None]
    peak = {k: max((r[k] for r in hourly), default=0) for k in KEYS}
    for r in hourly:
        r["ratio"] = {k: (round(r[k] / peak[k], 2) if peak[k] else None) for k in KEYS}
    now_hour = datetime.now(KST).hour if selectdate == "0" else None
    return {"selectdate": selectdate, "count": len(rows), "rows": rows, "peak": peak,
            "current_hour": now_hour,
            "note": "예상 인원은 필드(dg=출국, eg=입국)를 합산한 값입니다. 혼잡 기준값이 없어 하루 최대 대비 비율로 비교합니다."}


register(Tool(
    name="get_incheon_passenger_forecast",
    description="인천공항 터미널별 출국·입국 예상 승객 수(시간대별)를 볼 때 쓴다. 혼잡도 API 가 없는 인천의 혼잡 참고 자료. "
                "selectdate 0=오늘, 1=내일.",
    parameters={"type": "object", "properties": {"selectdate": {"type": "string", "enum": ["0", "1"]}}},
    func=get_incheon_passenger_forecast))
