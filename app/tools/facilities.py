"""인천공항 시설 안내 (인천국제공항공사 StatusOfFacility, 입국장 A · 출국장 D).

면세점·식당·편의점·약국·환전·라운지 같은 시설의 이름, 취급 품목, 위치, 운영 시간을 준다.
자료가 자주 바뀌지 않아 6시간 동안 같은 응답을 재사용한다 (하루 1000건 한도 보호).
분류(category)·터미널·층은 이름과 위치 글자에서 읽은 값이며, 읽지 못하면 비워 둔다.
"""
import re

from app.tools.common import fetch, pick
from app.tools.registry import Tool, register

URL = "https://apis.data.go.kr/B551177/StatusOfFacility/getFacilityKR"
PAGE = 500
MAX_PAGES = 4
TTL = 6 * 3600

CATEGORIES = [
    ("면세점", ("면세", "듀티", "duty")),
    ("약국·의료", ("약국", "의료", "병원", "클리닉", "의원", "응급")),
    ("환전·은행", ("환전", "은행", "atm", "금융", "외환", "현금")),
    ("라운지·휴식", ("라운지", "휴게", "수면", "샤워", "사우나", "캡슐", "쉼터", "휴식", "릴렉스")),
    ("식당·카페", ("식당", "카페", "커피", "푸드", "레스토랑", "베이커리", "버거", "음식", "한식", "중식", "일식",
                 "분식", "델리", "디저트", "아이스크림", "도넛", "치킨", "국수", "비스트로", "펍", "주점", "베이글")),
    ("편의점·쇼핑", ("편의점", "서점", "기념품", "쇼핑", "화장품", "패션", "잡화", "전자", "토이", "완구", "꽃")),
    ("편의시설", ("화장실", "수유", "흡연", "유아", "키즈", "놀이", "로밍", "통신", "택배", "수하물", "보관", "세탁",
                "렌터카", "주차", "안내", "충전", "샤워실", "기도", "의자")),
]
AREA = {"D": "출국장", "A": "입국장"}


def classify(name, items):
    text = f"{name or ''} {items or ''}".lower()
    for label, words in CATEGORIES:
        if any(w in text for w in words):
            return label
    return "기타"


def parse_location(text):
    """'탑승동 3층 동편 107번 탑승구 부근' → (탑승동, 3F)."""
    t = str(text or "")
    terminal = None
    if re.search(r"제\s*1\s*(여객)?\s*터미널|T1|1터미널", t, re.IGNORECASE):
        terminal = "T1"
    elif re.search(r"제\s*2\s*(여객)?\s*터미널|T2|2터미널", t, re.IGNORECASE):
        terminal = "T2"
    elif "탑승동" in t:
        terminal = "탑승동"
    floor = None
    m = re.search(r"(지하\s*(\d)|B(\d))\s*층?|(\d)\s*층", t)
    if m:
        floor = f"B{m.group(2) or m.group(3)}" if (m.group(2) or m.group(3)) else f"{m.group(4)}F"
    return terminal, floor


def _fetch_all():
    items, total = [], None
    for page in range(1, MAX_PAGES + 1):
        res = fetch(URL, {"numOfRows": PAGE, "pageNo": page}, ttl=TTL)
        items.extend(res["items"])
        total = res.get("total") or total
        try:
            expected = int(total)
        except (TypeError, ValueError):
            break
        if not res["items"] or len(items) >= expected:
            break
    return items, total


def get_incheon_facilities(keyword=None, area=None, category=None, limit=None):
    if area and area not in ("D", "A"):
        raise ValueError("area 는 D(출국장) 또는 A(입국장)이어야 합니다.")
    items, total = _fetch_all()
    rows = []
    for i in items:
        name = pick(i, "entrpskoreannm")
        goods = pick(i, "trtmntprdlstkoreannm")
        loc = pick(i, "lckoreannm")
        terminal, floor = parse_location(loc)
        code = (pick(i, "arrordep") or "").upper()
        rows.append({"name": name, "items": goods, "location": loc, "hours": pick(i, "servicetime"),
                     "tel": pick(i, "tel"), "area": code or None, "area_name": AREA.get(code),
                     "category": classify(name, goods), "terminal": terminal, "floor": floor})
    if area:
        rows = [r for r in rows if r["area"] == area]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if keyword:
        k = keyword.strip().lower()
        kcat = classify(keyword, None)
        rows = [r for r in rows if k in " ".join(str(r[f] or "") for f in ("name", "items", "location", "category")).lower()
                or (kcat != "기타" and r["category"] == kcat)]
    summary = {"by_category": {}, "by_area": {}, "by_place": {}}
    for r in rows:
        summary["by_category"][r["category"]] = summary["by_category"].get(r["category"], 0) + 1
        a = r["area_name"] or "기타"
        summary["by_area"][a] = summary["by_area"].get(a, 0) + 1
        place = f"{r['terminal'] or '기타'}|{r['floor'] or '-'}"
        bucket = summary["by_place"].setdefault(place, {})
        bucket[r["category"]] = bucket.get(r["category"], 0) + 1
    count = len(rows)
    if limit:
        rows = rows[: int(limit)]
    return {"airport": "ICN", "count": count, "total": total, "rows": rows, "summary": summary,
            "note": "분류·터미널·층은 시설 이름과 위치 글자에서 읽은 값입니다."}


register(Tool(
    name="get_incheon_facilities",
    description="인천공항의 면세점·식당·카페·약국·환전·라운지·편의시설 위치와 운영 시간을 찾을 때 쓴다. "
                "keyword(예: 면세, 약국), area(D=출국장, A=입국장), category 로 거를 수 있다.",
    parameters={"type": "object", "properties": {
        "keyword": {"type": "string"}, "area": {"type": "string", "enum": ["D", "A"]},
        "category": {"type": "string"}, "limit": {"type": "integer"}}},
    func=get_incheon_facilities))
