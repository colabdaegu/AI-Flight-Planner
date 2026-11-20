"""한국공항공사 공항 매장·시설 (전국공항 임대계약 정보 서비스, airport-lease-contract).

예전 '공항 상업시설 임대계약'(airport-retail-contract)은 승인 뒤에도 게이트웨이 코드 04 로 실패했고
공항 구분이 없었다. 이 서비스는 공항 코드로 거를 수 있고 업체명·업종·임대 위치(예: '국제선 1층 서편')를 준다.

- 계약 진행 중인 것만 받는다 (contractStatus=ON, 응답의 계약상태·종료일로 한 번 더 확인).
- 화물창고·사무실 같은 업무시설은 business=True 로 표시하고, 화면은 기본으로 승객용 매장만 보여 준다.
- 분류(category)는 인천 시설 안내와 같은 이름을 쓰고, 구역(국내선/국제선)·층은 위치 글자에서 읽는다.
"""
import re

from app import airports
from app.tools.common import fetch, maps_link, pick
from app.tools.flights import today_kst
from app.tools.registry import Tool, register

URL = "https://apis.data.go.kr/B551178/airport-lease-contract/info"
PAGE = 100
MAX_PAGES = 10
TTL = 6 * 3600

BUSINESS = ("화물", "창고", "사무", "업무", "정비", "급유", "격납", "주유", "물류", "기내식", "조업", "하역", "부지", "토지",
            "건물", "통신실", "전산", "기계실", "주차장", "광고")
GROUPS = [
    ("면세점", ("면세",)),
    ("약국·의료", ("약국", "의료", "병원", "의원", "클리닉")),
    ("환전·은행", ("환전", "은행", "금융", "외환", "atm")),
    ("라운지·휴식", ("라운지", "휴게", "수면", "샤워", "캡슐", "안내시설")),
    ("식당·카페", ("식음료", "음식", "식당", "카페", "커피", "제과", "베이커리", "푸드", "버거", "레스토랑", "주점")),
    ("편의점·쇼핑", ("편의점", "판매", "소매", "잡화", "서점", "기념품", "화장품", "의류", "토산품", "특산품", "쇼핑")),
    ("편의시설", ("렌터카", "렌트", "로밍", "통신", "택배", "수하물", "보관", "세탁", "이발", "미용", "자판기", "유아", "보험")),
]


def classify(store, business_type):
    text = f"{store or ''} {business_type or ''}".lower()
    if any(w in text for w in BUSINESS):
        return "업무시설", True
    for label, words in GROUPS:
        if any(w in text for w in words):
            return label, False
    return "기타", False


def parse_location(text):
    """'국제선 격리 3층' → ('국제선', '3F'), '국내선 1층 서편' → ('국내선', '1F')."""
    t = str(text or "")
    zone = "국제선" if "국제선" in t else "국내선" if "국내선" in t else None
    m = re.search(r"(지하\s*(\d)|B(\d))\s*층?|(\d)\s*층", t)
    floor = None
    if m:
        floor = f"B{m.group(2) or m.group(3)}" if (m.group(2) or m.group(3)) else f"{m.group(4)}F"
    return zone, floor


def _fetch_all(code):
    items, total = [], None
    for page in range(1, MAX_PAGES + 1):
        params = {"schAirportCode": code, "contractStatus": "ON", "numOfRows": PAGE, "pageNo": page}
        res = fetch(URL, params, ttl=TTL)
        items.extend(res["items"])
        total = res.get("total") or total
        try:
            expected = int(total)
        except (TypeError, ValueError):
            break
        if not res["items"] or len(items) >= expected:
            break
    return items, total


def get_commercial_facilities(airport, keyword=None, include_business=False):
    code = (airport or "").strip().upper()
    if code not in airports.KAC_AIRPORTS:
        raise ValueError("한국공항공사 공항(김포·김해·제주 등 14곳) 코드를 주십시오. 인천은 get_incheon_facilities 를 씁니다.")
    items, total = _fetch_all(code)
    today = today_kst()
    rows = []
    for i in items:
        if (pick(i, "airportCode") or code).upper() != code:
            continue
        end = re.sub(r"\D", "", pick(i, "contractEndDate") or "")[:8]
        status = pick(i, "contractStatus")
        if (end and end < today) or (status and "종료" in str(status)):
            continue
        store, kind, loc = pick(i, "storeName"), pick(i, "typeOfBusiness"), pick(i, "leaseLocation")
        category, business = classify(store, kind)
        zone, floor = parse_location(loc)
        rows.append({"name": store, "business_type": kind, "location": loc, "category": category,
                     "business": business, "zone": zone, "floor": floor,
                     "contract_start": pick(i, "contractStartDate"), "contract_end": pick(i, "contractEndDate"),
                     "map_url": maps_link(f"{airports.NAMES[code]}공항 {store}") if store else None})
    if keyword:
        k = keyword.strip().lower()
        kcat, _ = classify(keyword, None)          # '식당' → 식당·카페 처럼 종류로도 찾는다
        rows = [r for r in rows if k in " ".join(str(r[f] or "") for f in ("name", "business_type", "location", "category")).lower()
                or (kcat not in ("기타", "업무시설") and r["category"] == kcat)]
    hidden = 0 if include_business else sum(1 for r in rows if r["business"])
    shown = rows if include_business else [r for r in rows if not r["business"]]
    summary = {"by_category": {}, "by_place": {}}
    for r in shown:
        summary["by_category"][r["category"]] = summary["by_category"].get(r["category"], 0) + 1
        bucket = summary["by_place"].setdefault(f"{r['zone'] or '기타'}|{r['floor'] or '-'}", {})
        bucket[r["category"]] = bucket.get(r["category"], 0) + 1
    note = ("한국공항공사 전국공항 임대계약 정보(계약 진행 중)입니다. 운영 시간은 이 자료에 없습니다."
            if shown else f"결과 없음: {airports.NAMES[code]}공항 매장 자료에 '{keyword or '승객용 매장'}'에 맞는 곳이 없습니다.")
    return {"airport": code, "airport_name": airports.NAMES[code], "count": len(shown), "total": total,
            "hidden_business": hidden, "rows": shown, "summary": summary, "empty": not shown, "note": note}


register(Tool(
    name="get_commercial_facilities",
    description="김포·김해·제주 등 한국공항공사 공항의 매장(식당·카페·편의점·면세점·라운지 등) 이름·업종·위치를 찾을 때 쓴다. "
                "인천공항은 get_incheon_facilities 를 쓴다.",
    parameters={"type": "object", "properties": {
        "airport": {"type": "string", "description": "IATA 코드 (GMP, PUS, CJU …)"},
        "keyword": {"type": "string"}, "include_business": {"type": "boolean", "description": "업무시설(화물·사무실 등) 포함"}},
        "required": ["airport"]},
    func=get_commercial_facilities))
