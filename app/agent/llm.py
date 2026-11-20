"""요청 분석 (명세 F3005). GMS_API_KEY 가 있으면 LLM 구조화 출력, 없거나 실패하면 규칙 기반.

- LLM 은 문장을 '의도 + 조건'으로 바꾸는 데만 쓴다. 조회·계산·판단은 도구와 코드가 한다.
- 말하지 않은 조건은 채우지 않는다 (null / false). 뽑은 값은 normalize 로 형식을 다시 검사한다.
- 직전 대화 조건을 함께 넘겨 '내일은?' 같은 후속 요청에서 바뀐 값만 채우게 한다 (F3010).
"""
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel

from app import airports, config
from app.tools.flights import KST

INTENTS = ("departures", "arrivals", "flight_detail", "congestion", "passenger",
           "facilities", "plan", "greeting", "unknown")
ALIASES = {"commercial": "facilities", "flight_status": "flight_detail", "fastest": "departures",
           "departure": "departures", "arrival": "arrivals", "facility": "facilities"}
WEEKDAYS = "월화수목금토일"


class Intent(BaseModel):
    intent: str = "unknown"
    airport: Optional[str] = None            # 기준 공항 IATA (출발 조회=출발 공항, 도착 조회=도착 공항, 시설·혼잡=그 공항)
    destination: Optional[str] = None        # 출발 조회의 목적지 (IATA 코드 또는 도시 이름)
    origin: Optional[str] = None             # 도착 조회의 출발지 (IATA 코드 또는 도시 이름)
    flight_no: Optional[str] = None
    date: Optional[str] = None               # YYYYMMDD
    flexible_date: bool = False              # '빠른 시일 내', '날짜 상관없이'
    from_time: Optional[str] = None          # HHMM
    to_time: Optional[str] = None            # HHMM
    earliest: bool = False                   # '가장 빠른', '제일 먼저'
    arrive_airport_at: Optional[str] = None  # 사용자가 공항에 도착하는 시각 HHMM
    buffer_min: Optional[int] = None         # 체크인·보안검색 등 준비 시간(분)
    keyword: Optional[str] = None            # 시설 키워드 (면세점, 약국 …)
    reason: Optional[str] = None             # 분류 이유 한 문장 (실행 로그에 남긴다)


def now_kst():
    return datetime.now(KST)


def _shift(ymd, days):
    return (datetime.strptime(ymd, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _valid_date(v):
    try:
        datetime.strptime(v, "%Y%m%d")
        return True
    except (TypeError, ValueError):
        return False


def _valid_hhmm(v):
    return bool(v) and re.fullmatch(r"([01]\d|2[0-4])[0-5]\d", v) is not None


def normalize(it):
    """LLM·규칙 결과의 형식을 검사하고 틀린 값은 버린다 (임의로 고쳐 채우지 않는다)."""
    d = it.model_dump()
    kind = ALIASES.get(str(d.get("intent") or "").strip().lower(), str(d.get("intent") or "").strip().lower())
    d["intent"] = kind if kind in INTENTS else "unknown"
    code = str(d.get("airport") or "").strip().upper()
    if code and code not in airports.NAMES:
        codes = [c for c in airports.resolve_codes(d["airport"]) if c in airports.NAMES]
        code = codes[0] if codes else ""
    d["airport"] = code or None
    for k in ("destination", "origin", "keyword", "reason"):
        d[k] = (str(d[k]).strip() or None) if d.get(k) is not None else None
    fn = re.sub(r"\s", "", str(d.get("flight_no") or "")).upper()
    d["flight_no"] = fn if re.fullmatch(r"[A-Z0-9]{2}\d{1,4}[A-Z]?", fn) else None
    d["date"] = d["date"] if _valid_date(d.get("date")) else None
    for k in ("from_time", "to_time", "arrive_airport_at"):
        v = re.sub(r"\D", "", str(d.get(k) or ""))
        d[k] = v if _valid_hhmm(v) else None
    try:
        b = int(d.get("buffer_min")) if d.get("buffer_min") is not None else None
        d["buffer_min"] = b if b is not None and 0 <= b <= 600 else None
    except (TypeError, ValueError):
        d["buffer_min"] = None
    d["flexible_date"] = bool(d.get("flexible_date"))
    d["earliest"] = bool(d.get("earliest")) or d["flexible_date"]
    return Intent(**d)


# ---------------- 규칙 기반 (LLM 이 없거나 실패할 때) ----------------
def _clock(h, m, pm):
    h = int(h)
    if pm and h < 12:
        h += 12
    return f"{h:02d}{int(m or 0):02d}" if 0 <= h <= 24 else None


def _times(t):
    """문장 속 시각들. '오전/오후'가 없으면 앞에 나온 '오후'를 이어받고('오후 3시 이후 … 1시 반 도착' → 13:30),
    그것도 없으면 1~6시는 오후로 본다(한국어 일상 표현: '3시 출발' ≈ 15:00). '새벽·아침·오전'은 오전 그대로.
    '1시간'처럼 시간 길이는 시각으로 보지 않는다."""
    out = []
    period = None
    for m in re.finditer(r"(오전|오후|저녁|밤|새벽|아침)?\s*(\d{1,2})\s*(?::(\d{2})|시(?!간)\s*(?:(\d{1,2})\s*분|반)?)", t):
        mark = m.group(1)
        if mark:
            period = "pm" if mark in ("오후", "저녁", "밤") else "am"
        hour = int(m.group(2))
        if mark:
            pm = period == "pm"
        elif m.group(3):                      # 13:30 같은 24시간 표기는 그대로
            pm = False
        elif period == "pm":
            pm = hour < 12
        elif period == "am":
            pm = False
        else:
            pm = 1 <= hour <= 6
        minute = m.group(3) or m.group(4) or ("30" if "반" in m.group(0) else "0")
        v = _clock(m.group(2), minute, pm)
        if v:
            out.append((m.start(), m.end(), v))
    return out


def rule_intent(text, today=None, now=None):
    t = (text or "").strip()
    today = today or now_kst().strftime("%Y%m%d")

    fm = re.search(r"(?<![A-Za-z])([A-Za-z]{2}|[A-Za-z]\d|\d[A-Za-z])\s?(\d{1,4})(?!\d)", t)
    flight_no = f"{fm.group(1).upper()}{fm.group(2)}" if fm and re.search(r"[A-Za-z]", fm.group(1)) else None

    date = None
    dm = re.search(r"(20\d{2})[-.]?(\d{2})[-.]?(\d{2})", t)
    md = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if dm:
        date = "".join(dm.groups())
    elif md:
        y = int(today[:4])
        cand = f"{y}{int(md.group(1)):02d}{int(md.group(2)):02d}"
        date = cand if cand >= today else f"{y + 1}{int(md.group(1)):02d}{int(md.group(2)):02d}"
    elif "모레" in t:
        date = _shift(today, 2)
    elif "내일" in t:
        date = _shift(today, 1)
    elif "오늘" in t:
        date = today
    if date and not _valid_date(date):
        date = None

    times = _times(t)
    arrive_at = None
    from_time = to_time = None
    for start, end, v in times:
        after = t[end:end + 12]
        if re.match(r"\s*(?:에|까지)?\s*(?:공항\S*\s*)?도착", after) or re.search(r"공항\S*\s*$", t[max(0, start - 8):start]):
            arrive_at = arrive_at or v
        elif re.match(r"\s*(?:이후|부터|넘어서|넘어|후)", after):
            from_time = from_time or v
        elif re.match(r"\s*(?:전|까지|이전)", after):
            to_time = to_time or v
        elif from_time is None:
            from_time = v
    rng = re.search(r"(\d{1,2}):(\d{2})\s*[~\-]\s*(\d{1,2}):(\d{2})", t)
    if rng:
        from_time = f"{int(rng.group(1)):02d}{rng.group(2)}"
        to_time = f"{int(rng.group(3)):02d}{rng.group(4)}"

    buffer_min = None
    # '체크인에 2시간', '준비는 1시간이면 돼', '1시간 반 정도 여유', '90분 필요' → 준비 시간(분)
    bm = (re.search(r"(?:체크인|준비|보안\s*검색|수속|여유)\D{0,10}?(\d+(?:\.\d)?)\s*(시간|분)(\s*반)?", t)
          or re.search(r"(\d+(?:\.\d)?)\s*(시간|분)(\s*반)?\s*(?:정도|쯤)?\s*(?:걸|필요|소요|준비|여유|전)", t))
    if bm and re.search(r"체크인|준비|보안|수속|여유", t):
        buffer_min = int(float(bm.group(1)) * (60 if bm.group(2) == "시간" else 1)) + (30 if bm.group(3) and bm.group(2) == "시간" else 0)

    earliest = bool(re.search(r"가장\s*빠른|제일\s*빠른|제일\s*먼저|가장\s*먼저|빨리|빠른\s*시일|최대한\s*빨리|첫\s*편", t))
    flexible = bool(re.search(r"빠른\s*시일|아무\s*때|언제든|날짜\s*(?:상관|무관)|가능한\s*빨리|최대한\s*빨리", t))

    found = airports.find_in_text(t)
    domestic = [h for h in found if h["domestic"]]
    foreign = [h for h in found if not h["domestic"]]
    airport = destination = origin = None
    # 도착 편 조회 표현. '공항에 9시 도착하는데'처럼 사용자 자신의 도착은 제외한다.
    arrival_words = bool(re.search(r"들어오는|입국\s*편|\S(?<!출)발\s*(?:항공편|편|비행기)", t)) or (
        not arrive_at and bool(re.search(r"도착\s*(?:편|시간표|예정\s*편)|도착하는\s*(?:\S+\s*)?(?:항공편|편|비행기)", t)))

    def first_group(hits):
        if not hits:
            return []
        pos = hits[0]["pos"]
        return [h["code"] for h in hits if h["pos"] == pos]

    def marked(code, words):
        for h in found:
            if h["code"] == code:
                return re.match(rf"\S*\s*(?:{words})", t[h["pos"]:h["pos"] + 12]) is not None
        return False

    if foreign:
        dest_codes = first_group(foreign)
        if arrival_words:
            origin = ",".join(dest_codes)
        else:
            destination = ",".join(dest_codes)
        if domestic:
            group = first_group(domestic)
            airport = group[0] if len(group) == 1 else None
            if len(group) > 1:   # '서울' → 국제선이면 인천
                airport = "ICN" if "ICN" in group else group[0]
    elif len(domestic) >= 2:
        codes = [h["code"] for h in domestic]
        src = next((c for c in codes if marked(c, "에서|출발|발")), codes[0])
        dst = next((c for c in codes if c != src), None)
        if arrival_words:
            airport, origin = dst, src
        else:
            airport, destination = src, dst
    elif domestic:
        group = first_group(domestic)
        code = group[0]
        if len(group) > 1:
            code = "GMP" if "GMP" in group else group[0]
        if marked(code, "로|으로|행|까지|가는|갈|에\\s*가"):
            destination = code
        else:
            airport = code

    keyword = None
    km = re.search(r"(면세점|면세|식당|카페|커피|약국|환전|은행|라운지|편의점|화장실|수유실|흡연|샤워|기념품|서점|ATM|맛집)", t, re.IGNORECASE)
    if km:
        keyword = km.group(1)

    if re.search(r"^(안녕|하이|고마|감사|도움말|사용법)|무엇을 할 수|뭐 할 수|뭘 할 수|어떻게 써", t):
        kind = "greeting"
    elif flight_no and not destination:
        kind = "flight_detail"
    elif re.search(r"혼잡|붐비|붐벼|붐빈|북적|대기\s*시간|줄이\s*길|사람\s*많", t):
        kind = "congestion"
    elif "승객" in t or "출입국 예" in t or "예상 인원" in t:
        kind = "passenger"
    elif keyword or re.search(r"시설|매장|상업|위치 알려|어디 있", t):
        kind = "facilities"
    elif re.search(r"체크인\s*마감|출국\s*계획|계획\s*(?:저장|세워|짜)|몇 시까지 공항", t):
        kind = "plan"
    elif arrival_words:
        kind = "arrivals"
    elif destination or airport or re.search(r"출발|시간표|운항|항공편|비행기|가고\s*싶|여행|떠나", t):
        kind = "departures"
    else:
        kind = "unknown"
    if kind == "arrivals" and destination and not origin:
        origin, destination = destination, None

    return normalize(Intent(intent=kind, airport=airport, destination=destination, origin=origin,
                            flight_no=flight_no, date=date, flexible_date=flexible, from_time=from_time,
                            to_time=to_time, earliest=earliest, arrive_airport_at=arrive_at,
                            buffer_min=buffer_min, keyword=keyword, reason="규칙 기반 분석"))


# ---------------- LLM ----------------
def system_prompt(previous=None, now=None):
    now = now or now_kst()
    prev = ""
    if previous:
        keep = {k: v for k, v in previous.items() if v not in (None, False, "") and k != "reason"}
        prev = ("\n직전 대화에서 확정한 조건: " + json.dumps(keep, ensure_ascii=False) +
                "\n사용자가 일부만 바꿔 말하면('내일은?', '김포에서는?', '다른 편은?') 직전 intent 를 이어받고 "
                "이번에 말한 값만 채운다. 이어받을 나머지 값은 null 로 둔다(서버가 채운다).")
    return (
        "너는 '항공 여행 일정 플래너'의 요청 분석기다. 사용자 문장을 정해진 필드로 구조화만 한다. "
        "값을 지어내지 말고, 말하지 않은 값은 null(불리언은 false)로 둔다.\n"
        f"지금은 {now.strftime('%Y-%m-%d')}({WEEKDAYS[now.weekday()]}요일) {now.strftime('%H:%M')}, 한국 시간이다.\n"
        "intent 는 다음 중 하나:\n"
        "- departures: 어느 공항에서 출발하는 항공편, 어디로 가는 항공편, 가장 빠른 편 찾기, 여행 가고 싶다\n"
        "- arrivals: 공항에 도착하는(들어오는) 항공편\n"
        "- flight_detail: 특정 편명(KE123 등)의 시간·상태·탑승구\n"
        "- congestion: 공항이 붐비는지, 혼잡도, 대기\n"
        "- passenger: 인천공항 시간대별 출국·입국 예상 승객 수\n"
        "- facilities: 면세점·식당·카페·약국·환전·라운지·편의시설 위치와 운영 시간\n"
        "- plan: 체크인 마감 계산, 출국 계획 저장 방법\n"
        "- greeting: 인사, 감사, 사용법 질문\n"
        "- unknown: 공항·항공편과 무관한 요청\n"
        "airport 는 한국 공항 IATA 코드만: 인천 ICN, 김포 GMP, 김해(부산) PUS, 제주 CJU, 대구 TAE, 청주 CJJ, "
        "광주 KWJ, 울산 USN, 사천 HIN, 여수 RSU, 원주 WJU, 양양 YNY, 포항 KPO, 군산 KUV, 무안 MWX. "
        "서울에서 국제선이면 ICN, 국내선이면 GMP.\n"
        "departures 에서 출발 공항을 말하지 않았으면 airport 는 null 이다(임의로 인천을 넣지 마라).\n"
        "destination·origin 은 도시나 공항 이름을 사용자 표현 그대로 적거나, 확실할 때만 IATA 코드로 적는다 (예: 센다이 → '센다이' 또는 SDJ).\n"
        "'가장 빠른', '제일 먼저', '빨리' → earliest=true. '빠른 시일 내', '날짜 상관없이' → flexible_date=true, earliest=true.\n"
        "'공항에 9시 도착' → arrive_airport_at='0900'. '체크인에 2시간', '준비는 2시간이면 돼' → buffer_min=120. "
        "'오후 3시 이후' → from_time='1500'. '7시 전에', '7시까지' → to_time.\n"
        "오전·오후를 말하지 않은 시각은 앞에서 말한 오전·오후를 따른다 ('오후 3시 이후, 공항엔 1시 반 도착' → 1330). "
        "'1시간', '90분' 같은 시간 길이는 시각이 아니다.\n"
        "date 는 YYYYMMDD 로, 오늘·내일·모레·요일을 오늘 기준으로 계산한다. 시간은 HHMM.\n"
        "flight_no 는 공백 없이 대문자 (ke 123 → KE123). keyword 는 시설 종류(면세점, 약국 등).\n"
        "reason 에 이 intent 로 분류한 이유를 한 문장으로 적는다." + prev)


def _llm_intent(text, previous=None):
    from langchain_openai import ChatOpenAI          # 지연 import: 키가 없으면 설치 여부와 무관하게 동작

    llm = ChatOpenAI(model=config.GMS_MODEL, api_key=config.GMS_API_KEY, base_url=config.GMS_BASE_URL,
                     timeout=20, max_retries=1)
    return llm.with_structured_output(Intent).invoke(
        [("system", system_prompt(previous)), ("user", text)])


FILLABLE = ("airport", "destination", "origin", "flight_no", "date", "from_time", "to_time",
            "arrive_airport_at", "buffer_min", "keyword")


def reconcile(llm_it, rule_it, previous=None):
    """LLM 결과를 문장에서 규칙으로 직접 읽은 값과 맞춘다.

    - LLM 이 비워 둔 칸을 규칙이 문장에서 찾았으면 채운다 (문장에 있는 값이라 지어낸 것이 아님).
      예) 직전 대화 맥락 때문에 '대구에서 인천 가는 가장 빠른 항공편'의 출발·도착을 비워 버린 경우.
    - 출발 편 조회에서 출발 공항을 origin 에 넣었으면 airport 로 옮긴다 ('김해에서는?' → 출발 공항 김해).
    - LLM 이 직전 대화 값을 그대로 베껴 왔는데, 이번 문장에는 다른 값이 직접 적혀 있으면 문장 값을 쓴다.
      예) '내일 김포에서 제주' 다음 '김해에서는?' → LLM 이 출발 공항을 직전 값(GMP)으로 채워도 문장의 김해(PUS)를 쓴다.
    - LLM 이 분류하지 못했는데(unknown) 규칙이 분류했으면 규칙의 의도를 쓴다.
    반환: (Intent, 규칙으로 채운 칸 목록)"""
    d = llm_it.model_dump()
    r = rule_it.model_dump()
    filled = []
    if d["intent"] in ("unknown",) and r["intent"] not in ("unknown",):
        d["intent"] = r["intent"]
        filled.append("intent")
    if d["intent"] == "departures" and not d.get("airport") and d.get("origin"):
        codes = [c for c in airports.resolve_codes(d["origin"]) if c in airports.NAMES]
        if codes:
            d["airport"], d["origin"] = codes[0], None
            filled.append("airport←origin")
    if d["intent"] == "arrivals" and not d.get("airport") and d.get("destination"):
        codes = [c for c in airports.resolve_codes(d["destination"]) if c in airports.NAMES]
        if codes:
            d["airport"], d["destination"] = codes[0], None
            filled.append("airport←destination")
    same_kind = r["intent"] == d["intent"] or r["intent"] == "unknown"
    prev = previous or {}
    for k in FILLABLE:
        said = r.get(k)
        if said in (None, "") or d.get(k) in (None, "") or d.get(k) == said or not same_kind:
            continue
        if prev.get(k) not in (None, "") and str(d.get(k)) == str(prev.get(k)):
            d[k] = said                       # 직전 값을 베낀 칸 → 이번 문장에 적힌 값
            filled.append(f"{k}(이번 문장)")
    for k in FILLABLE:
        if d.get(k) in (None, "") and r.get(k) not in (None, "") and same_kind:
            if k == "origin" and d["intent"] == "departures":
                continue
            d[k] = r[k]
            filled.append(k)
    if r.get("earliest") and not d.get("earliest"):
        d["earliest"] = True
    return normalize(Intent(**d)), filled


def extract(text, previous=None):
    """(Intent, 출처, 메모) 를 돌려준다. 출처는 'llm' 또는 'rule'. 메모는 규칙 기반으로 넘어간 이유."""
    if config.GMS_API_KEY:
        try:
            got = normalize(_llm_intent(text, previous))
            fixed, filled = reconcile(got, rule_intent(text), previous)
            if filled:
                fixed.reason = (fixed.reason or "") + f" (문장에서 직접 읽어 보완: {', '.join(filled)})"
            return fixed, "llm", None
        except Exception as e:                        # LLM 실패는 규칙 기반으로 넘긴다 (세부 내용은 숨김)
            print("[llm-fallback]", type(e).__name__)
            return rule_intent(text), "rule", f"LLM 호출 실패({type(e).__name__})로 규칙 기반 분석을 썼습니다."
    return rule_intent(text), "rule", "GMS_API_KEY 가 없어 규칙 기반 분석을 썼습니다."
