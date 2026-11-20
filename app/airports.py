"""공항 목록과 자료 제공 기관. 모든 코드는 IATA 기준.

- ICN(인천): 인천국제공항공사 API
- KAC_AIRPORTS(한국공항공사 14곳): 한국공항공사 API
  ※ 명세 문서에 나온 코드(GMP, PUS, CJU, TAE, CJJ)만 확인된 값이고, 나머지는 IATA 표준 코드입니다. 첫 호출에서 확인하세요.
- INTERNATIONAL: 목적지 검색용 해외 공항 (출발 자료는 없음). 한국인이 자주 가는 공항 위주의 목록입니다.
"""

import re

ICN = "ICN"
KAC_AIRPORTS = {
    "GMP": "김포", "PUS": "김해", "CJU": "제주", "TAE": "대구", "CJJ": "청주",
    "KWJ": "광주", "USN": "울산", "HIN": "사천", "RSU": "여수", "WJU": "원주",
    "YNY": "양양", "KPO": "포항", "KUV": "군산", "MWX": "무안",
}
# 혼잡도는 공항별로 v1 / v2 엔드포인트가 나뉘어 있다 (명세 1bc76eae)
CONGESTION_AIRPORTS = {"GMP": "v1", "CJU": "v1", "PUS": "v2", "TAE": "v2", "CJJ": "v2"}

# 짧은 이름 (입력 매칭용 별칭으로 계속 쓴다)
SHORT_NAMES = {ICN: "인천", **KAC_AIRPORTS}
# 응답·화면에 쓰는 공항의 정식 한국어 명칭 (예: 대구 → 대구국제공항)
NAMES = {
    ICN: "인천국제공항", "GMP": "김포국제공항", "PUS": "김해국제공항", "CJU": "제주국제공항",
    "TAE": "대구국제공항", "CJJ": "청주국제공항", "KWJ": "광주공항", "USN": "울산공항",
    "HIN": "사천공항", "RSU": "여수공항", "WJU": "원주공항", "YNY": "양양국제공항",
    "KPO": "포항경주공항", "KUV": "군산공항", "MWX": "무안국제공항",
}


def provider_of(code):
    if code == ICN:
        return "IIAC"
    if code in KAC_AIRPORTS:
        return "KAC"
    return None


# ---- 검색용 정보 (자동 완성) ----
DOMESTIC_EN = {
    "ICN": "Incheon", "GMP": "Gimpo", "PUS": "Gimhae Busan", "CJU": "Jeju", "TAE": "Daegu",
    "CJJ": "Cheongju", "KWJ": "Gwangju", "USN": "Ulsan", "HIN": "Sacheon Jinju", "RSU": "Yeosu",
    "WJU": "Wonju", "YNY": "Yangyang Gangneung", "KPO": "Pohang", "KUV": "Gunsan", "MWX": "Muan",
}
DOMESTIC_ALIAS = {
    "ICN": ["서울", "인천국제", "Seoul"], "GMP": ["서울", "김포국제", "Seoul"],
    "PUS": ["부산", "Busan"], "CJU": ["제주도", "Jeju"], "TAE": ["대구", "Daegu"],
    "CJJ": ["청주", "Cheongju"], "KWJ": ["광주", "Gwangju"], "USN": ["울산", "Ulsan"],
    "HIN": ["진주", "사천", "Jinju", "Sacheon"], "RSU": ["여수", "Yeosu"], "WJU": ["원주", "Wonju"],
    "YNY": ["양양", "강릉", "속초", "Gangneung", "Sokcho"], "KPO": ["포항", "Pohang"],
    "KUV": ["군산", "Gunsan"], "MWX": ["무안", "Muan"],
}
# (IATA, 한글 이름, 영문 이름, 검색 동의어)
INTERNATIONAL = [
    ("NRT", "도쿄(나리타)", "Tokyo Narita", ["나리타", "도쿄", "Tokyo", "Narita"]),
    ("HND", "도쿄(하네다)", "Tokyo Haneda", ["하네다", "도쿄", "Tokyo", "Haneda"]),
    ("KIX", "오사카(간사이)", "Osaka Kansai", ["간사이", "오사카", "Osaka", "Kansai"]),
    ("ITM", "오사카(이타미)", "Osaka Itami", ["이타미", "오사카", "Osaka", "Itami"]),
    ("NGO", "나고야(주부)", "Nagoya Chubu", ["나고야", "주부", "Nagoya"]),
    ("FUK", "후쿠오카", "Fukuoka", ["후쿠오카", "Fukuoka"]),
    ("CTS", "삿포로(신치토세)", "Sapporo New Chitose", ["삿포로", "신치토세", "Sapporo", "Chitose"]),
    ("OKA", "오키나와(나하)", "Okinawa Naha", ["오키나와", "나하", "Okinawa", "Naha"]),
    ("HIJ", "히로시마", "Hiroshima", ["히로시마", "Hiroshima"]),
    ("KMJ", "구마모토", "Kumamoto", ["구마모토", "Kumamoto"]),
    ("PEK", "베이징(수도)", "Beijing Capital", ["베이징", "북경", "Beijing", "Peking"]),
    ("PKX", "베이징(다싱)", "Beijing Daxing", ["베이징", "다싱", "Beijing"]),
    ("PVG", "상하이(푸동)", "Shanghai Pudong", ["상하이", "상해", "푸동", "Shanghai"]),
    ("SHA", "상하이(훙차오)", "Shanghai Hongqiao", ["상하이", "홍차오", "Shanghai"]),
    ("TAO", "칭다오", "Qingdao", ["칭다오", "Qingdao"]),
    ("DLC", "다롄", "Dalian", ["다롄", "Dalian"]),
    ("CAN", "광저우", "Guangzhou", ["광저우", "Guangzhou"]),
    ("SZX", "선전", "Shenzhen", ["선전", "Shenzhen"]),
    ("HKG", "홍콩", "Hong Kong", ["홍콩", "Hong Kong"]),
    ("MFM", "마카오", "Macau", ["마카오", "Macau"]),
    ("TPE", "타이베이(타오위안)", "Taipei Taoyuan", ["타이베이", "타이완", "대만", "Taipei", "Taiwan"]),
    ("BKK", "방콕(수완나품)", "Bangkok Suvarnabhumi", ["방콕", "수완나품", "Bangkok"]),
    ("DMK", "방콕(돈므앙)", "Bangkok Don Mueang", ["방콕", "돈므앙", "Bangkok"]),
    ("HKT", "푸켓", "Phuket", ["푸켓", "Phuket"]),
    ("CNX", "치앙마이", "Chiang Mai", ["치앙마이", "Chiang Mai"]),
    ("SGN", "호찌민", "Ho Chi Minh City", ["호찌민", "호치민", "사이공", "Ho Chi Minh", "Saigon"]),
    ("HAN", "하노이", "Hanoi", ["하노이", "Hanoi"]),
    ("DAD", "다낭", "Da Nang", ["다낭", "Da Nang", "Danang"]),
    ("CXR", "나트랑(깜라인)", "Nha Trang Cam Ranh", ["나트랑", "냐짱", "Nha Trang", "Cam Ranh"]),
    ("MNL", "마닐라", "Manila", ["마닐라", "Manila"]),
    ("CEB", "세부", "Cebu", ["세부", "Cebu"]),
    ("KLO", "보라카이(칼리보)", "Kalibo", ["보라카이", "Boracay", "Kalibo"]),
    ("DPS", "발리(덴파사르)", "Bali Denpasar", ["발리", "덴파사르", "Bali", "Denpasar"]),
    ("SIN", "싱가포르(창이)", "Singapore Changi", ["싱가포르", "창이", "Singapore"]),
    ("KUL", "쿠알라룸푸르", "Kuala Lumpur", ["쿠알라룸푸르", "말레이시아", "Kuala Lumpur", "Malaysia"]),
    ("CGK", "자카르타", "Jakarta", ["자카르타", "Jakarta"]),
    ("DEL", "델리", "New Delhi", ["델리", "인도", "Delhi", "India"]),
    ("VVO", "블라디보스토크", "Vladivostok", ["블라디보스토크", "Vladivostok"]),
    ("GUM", "괌", "Guam", ["괌", "Guam"]),
    ("SPN", "사이판", "Saipan", ["사이판", "Saipan"]),
    ("HNL", "호놀룰루", "Honolulu", ["호놀룰루", "하와이", "Honolulu", "Hawaii"]),
    ("LAX", "로스앤젤레스", "Los Angeles", ["로스앤젤레스", "LA", "Los Angeles"]),
    ("SFO", "샌프란시스코", "San Francisco", ["샌프란시스코", "San Francisco"]),
    ("SEA", "시애틀", "Seattle", ["시애틀", "Seattle"]),
    ("JFK", "뉴욕(JFK)", "New York JFK", ["뉴욕", "New York", "JFK"]),
    ("EWR", "뉴욕(뉴어크)", "Newark", ["뉴욕", "뉴어크", "Newark", "New York"]),
    ("ORD", "시카고", "Chicago O'Hare", ["시카고", "Chicago"]),
    ("DFW", "댈러스", "Dallas Fort Worth", ["댈러스", "Dallas"]),
    ("YVR", "밴쿠버", "Vancouver", ["밴쿠버", "Vancouver"]),
    ("YYZ", "토론토", "Toronto", ["토론토", "Toronto"]),
    ("LHR", "런던(히스로)", "London Heathrow", ["런던", "히스로", "London"]),
    ("CDG", "파리(샤를드골)", "Paris Charles de Gaulle", ["파리", "샤를드골", "Paris"]),
    ("FRA", "프랑크푸르트", "Frankfurt", ["프랑크푸르트", "Frankfurt"]),
    ("MUC", "뮌헨", "Munich", ["뮌헨", "Munich"]),
    ("AMS", "암스테르담", "Amsterdam", ["암스테르담", "Amsterdam"]),
    ("FCO", "로마(피우미치노)", "Rome Fiumicino", ["로마", "피우미치노", "Rome"]),
    ("IST", "이스탄불", "Istanbul", ["이스탄불", "Istanbul"]),
    ("DXB", "두바이", "Dubai", ["두바이", "Dubai"]),
    ("DOH", "도하", "Doha", ["도하", "카타르", "Doha", "Qatar"]),
    ("SYD", "시드니", "Sydney", ["시드니", "Sydney"]),
    ("MEL", "멜버른", "Melbourne", ["멜버른", "Melbourne"]),
    ("AKL", "오클랜드", "Auckland", ["오클랜드", "Auckland"]),
    # 일본 지방 공항
    ("SDJ", "센다이", "Sendai", ["센다이", "Sendai"]),
    ("KOJ", "가고시마", "Kagoshima", ["가고시마", "Kagoshima"]),
    ("MYJ", "마쓰야마", "Matsuyama", ["마쓰야마", "Matsuyama"]),
    ("TAK", "다카마쓰", "Takamatsu", ["다카마쓰", "Takamatsu"]),
    ("OIT", "오이타", "Oita", ["오이타", "Oita"]),
    ("NGS", "나가사키", "Nagasaki", ["나가사키", "Nagasaki"]),
    ("KIJ", "니가타", "Niigata", ["니가타", "Niigata"]),
    ("AOJ", "아오모리", "Aomori", ["아오모리", "Aomori"]),
    ("FSZ", "시즈오카", "Shizuoka", ["시즈오카", "Shizuoka"]),
    ("KMQ", "고마쓰(가나자와)", "Komatsu", ["고마쓰", "가나자와", "Komatsu", "Kanazawa"]),
    ("TOY", "도야마", "Toyama", ["도야마", "Toyama"]),
    ("YGJ", "요나고", "Yonago", ["요나고", "Yonago"]),
    ("UBJ", "야마구치우베", "Yamaguchi Ube", ["야마구치", "우베", "Ube"]),
    ("HKD", "하코다테", "Hakodate", ["하코다테", "Hakodate"]),
    ("OKJ", "오카야마", "Okayama", ["오카야마", "Okayama"]),
    ("HSG", "사가", "Saga", ["사가", "Saga"]),
    ("KKJ", "기타큐슈", "Kitakyushu", ["기타큐슈", "Kitakyushu"]),
    ("ISG", "이시가키", "Ishigaki", ["이시가키", "Ishigaki"]),
    # 중국·대만
    ("TSN", "톈진", "Tianjin", ["톈진", "천진", "Tianjin"]),
    ("SHE", "선양", "Shenyang", ["선양", "심양", "Shenyang"]),
    ("YNJ", "옌지", "Yanji", ["옌지", "연길", "Yanji"]),
    ("HRB", "하얼빈", "Harbin", ["하얼빈", "Harbin"]),
    ("CGQ", "창춘", "Changchun", ["창춘", "장춘", "Changchun"]),
    ("WEH", "웨이하이", "Weihai", ["웨이하이", "위해", "Weihai"]),
    ("YNT", "옌타이", "Yantai", ["옌타이", "연태", "Yantai"]),
    ("XIY", "시안", "Xi'an", ["시안", "서안", "Xian"]),
    ("CKG", "충칭", "Chongqing", ["충칭", "중경", "Chongqing"]),
    ("HGH", "항저우", "Hangzhou", ["항저우", "항주", "Hangzhou"]),
    ("NKG", "난징", "Nanjing", ["난징", "남경", "Nanjing"]),
    ("XMN", "샤먼", "Xiamen", ["샤먼", "하문", "Xiamen"]),
    ("KMG", "쿤밍", "Kunming", ["쿤밍", "곤명", "Kunming"]),
    ("SYX", "싼야", "Sanya", ["싼야", "하이난", "Sanya", "Hainan"]),
    ("KHH", "가오슝", "Kaohsiung", ["가오슝", "Kaohsiung"]),
    ("RMQ", "타이중", "Taichung", ["타이중", "Taichung"]),
    # 동남아·중앙아시아
    ("PQC", "푸꾸옥", "Phu Quoc", ["푸꾸옥", "푸쿠옥", "Phu Quoc"]),
    ("HPH", "하이퐁", "Hai Phong", ["하이퐁", "Haiphong"]),
    ("VTE", "비엔티안", "Vientiane", ["비엔티안", "라오스", "Vientiane", "Laos"]),
    ("RGN", "양곤", "Yangon", ["양곤", "미얀마", "Yangon"]),
    ("BKI", "코타키나발루", "Kota Kinabalu", ["코타키나발루", "Kota Kinabalu"]),
    ("PEN", "페낭", "Penang", ["페낭", "Penang"]),
    ("CRK", "클라크", "Clark", ["클라크", "Clark"]),
    ("UBN", "울란바토르", "Ulaanbaatar", ["울란바토르", "몽골", "Ulaanbaatar", "Mongolia"]),
    ("KTM", "카트만두", "Kathmandu", ["카트만두", "네팔", "Kathmandu", "Nepal"]),
    ("CMB", "콜롬보", "Colombo", ["콜롬보", "스리랑카", "Colombo"]),
    ("BOM", "뭄바이", "Mumbai", ["뭄바이", "Mumbai"]),
    ("TAS", "타슈켄트", "Tashkent", ["타슈켄트", "우즈베키스탄", "Tashkent"]),
    ("ALA", "알마티", "Almaty", ["알마티", "카자흐스탄", "Almaty"]),
    ("AUH", "아부다비", "Abu Dhabi", ["아부다비", "Abu Dhabi"]),
    # 유럽
    ("ZRH", "취리히", "Zurich", ["취리히", "스위스", "Zurich"]),
    ("VIE", "비엔나", "Vienna", ["오스트리아", "Vienna", "Wien"]),
    ("MAD", "마드리드", "Madrid", ["마드리드", "Madrid"]),
    ("BCN", "바르셀로나", "Barcelona", ["바르셀로나", "Barcelona"]),
    ("PRG", "프라하", "Prague", ["프라하", "체코", "Prague"]),
    ("HEL", "헬싱키", "Helsinki", ["헬싱키", "핀란드", "Helsinki"]),
    ("MXP", "밀라노(말펜사)", "Milan Malpensa", ["밀라노", "Milan"]),
    ("LIS", "리스본", "Lisbon", ["리스본", "포르투갈", "Lisbon"]),
    ("BUD", "부다페스트", "Budapest", ["부다페스트", "헝가리", "Budapest"]),
    ("WAW", "바르샤바", "Warsaw", ["바르샤바", "폴란드", "Warsaw"]),
    ("CPH", "코펜하겐", "Copenhagen", ["코펜하겐", "덴마크", "Copenhagen"]),
    ("ZAG", "자그레브", "Zagreb", ["자그레브", "크로아티아", "Zagreb"]),
    # 미주·대양주
    ("ATL", "애틀랜타", "Atlanta", ["애틀랜타", "Atlanta"]),
    ("IAD", "워싱턴(덜레스)", "Washington Dulles", ["워싱턴", "Washington"]),
    ("BOS", "보스턴", "Boston", ["보스턴", "Boston"]),
    ("LAS", "라스베이거스", "Las Vegas", ["라스베이거스", "Las Vegas"]),
    ("MEX", "멕시코시티", "Mexico City", ["멕시코시티", "멕시코", "Mexico City"]),
    ("BNE", "브리즈번", "Brisbane", ["브리즈번", "Brisbane"]),
    ("NAN", "난디(피지)", "Nadi", ["난디", "피지", "Nadi", "Fiji"]),
    ("ROR", "팔라우", "Palau", ["팔라우", "Palau"]),
]


def _entries():
    rows = []
    for code, ko in NAMES.items():
        rows.append({"code": code, "name": ko, "en": DOMESTIC_EN.get(code, code),
                     "alias": DOMESTIC_ALIAS.get(code, []) + [SHORT_NAMES[code]], "domestic": True,
                     "can_depart": provider_of(code) is not None})
    for code, ko, en, alias in INTERNATIONAL:
        rows.append({"code": code, "name": ko, "en": en, "alias": alias,
                     "domestic": False, "can_depart": False})
    return rows


_ENTRIES = _entries()


def _norm(text):
    """검색 비교용: 소문자, 공백 제거."""
    return "".join(str(text).lower().split())


def search(query, scope="all", limit=10):
    """공항 자동 완성 검색. 코드·한글 이름·영문 이름·동의어로 찾는다.
    scope='origin' 이면 출발 자료가 있는 공항(인천·한국공항공사 14곳)만 돌려준다.
    정확한 코드 > 이름 앞부분 > 이름 포함 순으로 정렬한다."""
    q = _norm(query or "")
    if not q:
        return []
    scored = []
    for e in _ENTRIES:
        if scope == "origin" and not e["can_depart"]:
            continue
        keys = [e["code"].lower(), _norm(e["name"]), _norm(e["en"])] + [_norm(a) for a in e["alias"]]
        if e["code"].lower() == q:
            rank = 0
        elif any(k.startswith(q) for k in keys):
            rank = 1
        elif any(q in k for k in keys):
            rank = 2
        else:
            continue
        scored.append((rank, e))
    scored.sort(key=lambda t: (t[0], t[1]["name"]))
    return [{"code": e["code"], "name": e["name"], "en": e["en"],
             "domestic": e["domestic"], "can_depart": e["can_depart"]}
            for _, e in scored[:limit]]


def _names(entry):
    """검색에 쓸 이름들. '도쿄(나리타)' 는 '도쿄', '나리타' 로도 찾는다."""
    out = {entry["name"], *entry["alias"]}
    for part in re.split(r"[()·/]", entry["name"]):
        if part.strip():
            out.add(part.strip())
    return out


# 평범한 낱말과 겹치는 이름: 뒤에 '로/에/행/공항' 같은 말이 붙을 때만 공항으로 본다 ('세부 일정', '사가지고')
AMBIGUOUS = {"세부", "사가", "선전", "시안", "주부", "우베", "나하", "다싱", "창이", "하문"}
_CONTEXT = r"(?=\s*(?:으로|로|에서|에|행|공항|까지|가는|갈|출발|도착|편|항공)|\s*[?!.]|\s*$)"


def find_in_text(text):
    """문장 속 공항 언급을 나온 순서대로 돌려준다: [{code, pos, domestic}].
    같은 자리에서 여러 공항이 걸리면(예: '도쿄' → NRT, HND) 모두 돌려준다."""
    t = text or ""
    low = t.lower()
    hits = {}
    for e in _ENTRIES:
        best = None
        m = re.search(rf"(?<![A-Za-z]){e['code']}(?![A-Za-z])", t)          # 대문자 코드만
        if m:
            best = m.start()
        for name in _names(e):
            if len(name) < 2 and name != e["name"]:
                continue
            if re.fullmatch(r"[A-Za-z' .]+", name):
                m = re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", low)
                pos = m.start() if m else -1
            elif name in AMBIGUOUS:
                m = re.search(re.escape(name) + _CONTEXT, t)
                pos = m.start() if m else -1
            else:
                pos = t.find(name)
            if pos >= 0 and (best is None or pos < best):
                best = pos
        if best is not None:
            hits[e["code"]] = {"code": e["code"], "pos": best, "domestic": e["domestic"]}
    return sorted(hits.values(), key=lambda h: (h["pos"], h["code"]))


def resolve_codes(text):
    """목적지·출발지 문자열을 IATA 코드 목록으로 바꾼다. 도시에 공항이 여럿이면 모두 돌려준다."""
    value = (text or "").strip()
    if not value:
        return []
    up = value.upper()
    known = {e["code"] for e in _ENTRIES}
    if re.fullmatch(r"[A-Z]{3}", up):
        return [up]
    found = find_in_text(value)
    if found:
        first = found[0]["pos"]
        return [h["code"] for h in found if h["pos"] == first]
    res = search(value, limit=1)
    if res:
        return [res[0]["code"]]
    return [up] if up in known else []


def name_of(code):
    for e in _ENTRIES:
        if e["code"] == code:
            return e["name"]
    return code


def is_domestic(code):
    return code in NAMES
