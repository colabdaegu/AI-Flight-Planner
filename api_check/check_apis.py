"""공공데이터 API 수신 확인: 인천 3개 + 한국공항공사 3개 (공항별)

명세: 제공받은 OpenAPI 활용가이드 캡처 6종 기준
  인천공항       : 항공기 운항현황(출발·도착), 승객예고(D+0·D+1), 상업시설(StatusOfFacility)
  한국공항공사   : 실시간 운항정보(출발·도착·국내정보·국제정보·상세), 공항 혼잡도(v1·v2), 공항상업시설 임대계약

실행 (프로젝트 루트, Git Bash)
    python api_check/check_apis.py
    python api_check/check_apis.py --group incheon
    python api_check/check_apis.py --group kac --airport GMP CJU

판정
    OK         호출 성공, 명세 항목 확인, 자료 있음
    NO_RESULT  호출 성공, 자료 0건 (실패 아님. 시각·공항에 따라 정상)
    CHECK      자료는 왔지만 명세 항목 이름이 다름 → 실제 항목 이름을 함께 표시
    FAIL       키·승인·주소·한도·응답 형식 문제 → 응답 앞부분을 함께 표시 (키는 가림)

키 값은 출력하지 않습니다 (NF3001, NF3002).
"""
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
KEY = os.getenv("DATA_GO_KR_KEY", "").strip()
TIMEOUT = 15
SPACING = 0.3                       # 호출 사이 대기 (동시 호출 제한 회피)
KST = timezone(timedelta(hours=9))

IIAC = "https://apis.data.go.kr/B551177"
KAC = "https://apis.data.go.kr/B551178"
KAC_AIRPORTS = ["GMP", "PUS", "CJU", "TAE", "CJJ", "KWJ", "USN", "HIN",
                "RSU", "WJU", "YNY", "KPO", "KUV", "MWX"]
CONGESTION = {"v1": ["GMP", "CJU"], "v2": ["PUS", "TAE", "CJJ"]}   # 명세: v1 김포·제주, v2 김해·청주·대구

PORTAL_ERRORS = {
    "Unauthorized": "인증키가 없거나 유효하지 않음 (.env 의 DATA_GO_KR_KEY 확인)",
    "Forbidden": "활용신청 내역이 없거나 승인 전 (포털 마이페이지에서 서비스별 승인 상태 확인)",
    "API not found": "호출 주소가 틀렸거나 폐기된 API",
    "API rate limit exceeded": "동시 호출 수 초과 — 잠시 후 다시 호출",
    "API token quota exceeded": "일일 호출 허용량 초과 — 다음 날 초기화",
}
PARAM = re.compile(r"(?i)(serviceKey)=([^&\s'\"]+)")


# ---------- 공통 ----------

def scrub(text):
    text = str(text)
    if KEY:
        text = text.replace(KEY, "***").replace(quote(KEY, safe=""), "***")
    return PARAM.sub(lambda m: f"{m.group(1)}=***", text)


def call(url, params):
    query = {"serviceKey": KEY, "type": "json", **params}
    try:
        r = requests.get(url, params=query, timeout=TIMEOUT)
    except requests.RequestException as e:
        time.sleep(SPACING)
        return {"error": f"요청 실패: {scrub(e)}"}
    time.sleep(SPACING)
    return {"http": r.status_code, "content": r.content}


def parse(content, text):
    """JSON 과 XML 을 같은 모양({code, msg, total, items})으로 맞춘다."""
    if text.startswith("{"):
        data = json.loads(text)
        gw = data.get("OpenAPI_ServiceResponse")
        if isinstance(gw, dict):                        # 게이트웨이 오류 (JSON)
            head = gw.get("cmmMsgHeader", gw) or {}
            return {"code": str(head.get("returnReasonCode") or "ERR"), "msg": head.get("returnAuthMsg") or "",
                    "total": None, "items": []}
        resp = data.get("response", data)
        header = resp.get("header", {}) or {}
        body = resp.get("body", {}) or {}
        node = body.get("items", "")
        if isinstance(node, dict):
            items = node.get("item", [])
        elif isinstance(node, list):            # items 가 목록을 바로 담는 응답 (인천 여객기 등)
            items = node
        else:
            items = []
        if isinstance(items, dict):
            items = [items]
        return {"code": str(header.get("resultCode", "") or ""), "msg": header.get("resultMsg", "") or "",
                "total": body.get("totalCount"), "items": items}
    root = ET.fromstring(content)
    if root.tag == "OpenAPI_ServiceResponse":           # 게이트웨이 오류 (XML)
        return {"code": root.findtext(".//returnReasonCode") or "ERR",
                "msg": root.findtext(".//returnAuthMsg") or "", "total": None, "items": []}
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return {"code": root.findtext(".//resultCode") or "", "msg": root.findtext(".//resultMsg") or "",
            "total": root.findtext(".//totalCount"), "items": items}


def get(item, name):
    lowered = {k.lower(): v for k, v in item.items()}
    return lowered.get(name.lower())


def missing(fields, item):
    keys = {k.lower() for k in item}
    miss = []
    for f in fields:
        names = f if isinstance(f, tuple) else (f,)
        if not any(n.lower() in keys for n in names):
            miss.append("/".join(names))
    return miss


def preview(fields, item):
    parts = []
    for f in fields[:6]:
        names = f if isinstance(f, tuple) else (f,)
        val = next((get(item, n) for n in names if get(item, n) not in (None, "")), None)
        parts.append(f"{names[0]}={str(val)[:20]}")
    return "  ".join(parts)


def judge(fields, raw):
    """(상태, 설명, 파싱 결과). 상태: OK · NO_RESULT · CHECK · FAIL"""
    if "error" in raw:
        return "FAIL", raw["error"], None
    text = raw["content"].decode("utf-8", "replace").strip()
    snippet = scrub(text[:200]).replace("\n", " ")
    if raw["http"] != 200:
        return "FAIL", f"HTTP {raw['http']} · {PORTAL_ERRORS.get(text, snippet)}", None
    if not text.startswith(("{", "<")):
        return "FAIL", PORTAL_ERRORS.get(text, f"예상 밖 응답: {snippet}"), None
    try:
        parsed = parse(raw["content"], text)
    except Exception as e:
        return "FAIL", f"응답 해석 실패: {scrub(e)} · 원문: {snippet}", None
    if not parsed["code"]:
        return "FAIL", f"결과코드 없음 (응답 형식이 명세와 다름) · 원문: {snippet}", None
    if parsed["code"] != "00":
        return "FAIL", f"제공기관 결과코드 {parsed['code']} · {parsed['msg']}", parsed
    if not parsed["items"]:
        return "NO_RESULT", "호출 성공, 자료 0건", parsed
    miss = missing(fields, parsed["items"][0])
    if miss:
        keys = ", ".join(list(parsed["items"][0].keys())[:20])
        return "CHECK", f"명세 항목 없음: {', '.join(miss)} · 실제 항목: {keys}", parsed
    return "OK", f"전체 {parsed['total']}건 중 {len(parsed['items'])}건 수신", parsed


def merged(params, override):
    out = dict(params)
    for k, v in override.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def describe(override):
    return ", ".join(f"{k}={v}" if v is not None else f"{k} 제외" for k, v in override.items())


def job(group, airport, service, url, params, fields, variants=None, summary=None, label=None):
    return {"group": group, "airport": airport, "service": service, "url": url, "params": params,
            "fields": fields, "variants": variants or [], "summary": summary,
            "label": label or f"{group} {airport} {service}"}


# ---------- 요약 함수 (자료가 온 뒤 한 줄 요약) ----------

def facility_summary(parsed):
    a = sum(1 for i in parsed["items"] if (get(i, "arrordep") or "").upper() == "A")
    d = sum(1 for i in parsed["items"] if (get(i, "arrordep") or "").upper() == "D")
    return f"입국장(A) {a}건 · 출국장(D) {d}건 (이 페이지 기준)"


def commercial_summary(parsed):
    today = datetime.now(KST).strftime("%Y%m%d")
    active = sum(1 for i in parsed["items"]
                 if re.sub(r"\D", "", get(i, "ContDate") or "")[:8] >= today)
    n = len(parsed["items"])
    return f"계약 유효 {active} · 만료 {n - active} (이 페이지 기준, 전체 {parsed['total']}건)"


def detail_summary(parsed):
    return (f"전체 {parsed['total']}건. 현재 앱 코드는 500건까지만 읽으므로, "
            f"전체가 500을 넘으면 편명 검색에서 누락될 수 있음")


# ---------- 작업 목록 ----------

def incheon_jobs(ymd):
    dep_params = {"searchDate": ymd, "searchFrom": "0000", "searchTo": "2359",
                  "searchdtCode": "E", "passengerOrCargo": "P", "numOfRows": 5, "pageNo": 1}
    dep_fields = ["flightId", "airline", "airportCode", "airport", "scheduleDatetime",
                  "estimatedDatetime", "gateNumber", "terminalId", "remark"]
    arr_params = {k: v for k, v in dep_params.items()}
    arr_params.pop("searchdtCode")
    arr_params["searchdtCode"] = "E"
    arr_fields = ["flightId", "airline", "airport", "scheduleDatetime",
                  "estimatedDatetime", "gateNumber", "terminalId", "remark"]
    pax_fields = ["adate", "atime", "t1egsum1", "t1dgsum1", "t2dgsum2"]
    fac_fields = ["entrpskoreannm", "trtmntprdlstkoreannm", "lckoreannm", "servicetime", "arrordep", "tel"]
    pax_flight_fields = ["flightId", "airline", "airportCode", "airport", "scheduleDateTime",
                         "estimatedDateTime", "gatenumber", "terminalid", "remark"]
    return [
        job("인천", "ICN", "출발", f"{IIAC}/statusOfAllFltDeOdp/getFltDeparturesDeOdp", dep_params, dep_fields,
            variants=[{"searchTo": "2400"}, {"searchdtCode": "S"}, {"searchFrom": None, "searchTo": None}],
            label="인천 운항현황 - 출발 (statusOfAllFltDeOdp)"),
        job("인천", "ICN", "도착", f"{IIAC}/statusOfAllFltDeOdp/getFltArrivalsDeOdp", arr_params, arr_fields,
            variants=[{"searchTo": "2400"}, {"searchdtCode": "S"}, {"searchFrom": None, "searchTo": None}],
            label="인천 운항현황 - 도착 (statusOfAllFltDeOdp)"),
        job("인천", "ICN", "승객예고 D+0", f"{IIAC}/passgrAnncmt/getPassgrAnncmt",
            {"selectdate": "0", "numOfRows": 10, "pageNo": 1}, pax_fields,
            label="인천 승객예고 오늘 (passgrAnncmt)"),
        job("인천", "ICN", "승객예고 D+1", f"{IIAC}/passgrAnncmt/getPassgrAnncmt",
            {"selectdate": "1", "numOfRows": 10, "pageNo": 1}, pax_fields,
            label="인천 승객예고 내일 (passgrAnncmt)"),
        job("인천", "ICN", "상업시설", f"{IIAC}/StatusOfFacility/getFacilityKR",
            {"numOfRows": 10, "pageNo": 1}, fac_fields, summary=facility_summary,
            label="인천 상업시설 정보 (StatusOfFacility, 입국장 A / 출국장 D)"),
        # 승인 목록의 "여객기 운항 현황 상세 조회" (StatusOfPassengerFlightsDeOdp). 파라미터 이름이 위와 다르다.
        job("인천", "ICN", "여객도착", f"{IIAC}/StatusOfPassengerFlightsDeOdp/getPassengerArrivalsDeOdp",
            {"searchday": ymd, "from_time": "0000", "to_time": "2400", "inqtimechcd": "E", "lang": "K",
             "numOfRows": 5, "pageNo": 1}, pax_flight_fields,
            variants=[{"inqtimechcd": "S"}],
            label="인천 여객기 도착 (StatusOfPassengerFlightsDeOdp)"),
        job("인천", "ICN", "여객출발", f"{IIAC}/StatusOfPassengerFlightsDeOdp/getPassengerDeparturesDeOdp",
            {"searchday": ymd, "from_time": "0000", "to_time": "2400", "inqtimechcd": "E", "lang": "K",
             "numOfRows": 5, "pageNo": 1}, pax_flight_fields,
            variants=[{"inqtimechcd": "S"}],
            label="인천 여객기 출발 (StatusOfPassengerFlightsDeOdp)"),
    ]


def kac_airport_jobs(code, ymd):
    base = {"searchday": ymd, "from_time": "0000", "to_time": "2359",
            "airport_code": code, "numOfRows": 5, "pageNo": 1}
    return [
        job("KAC", code, "출발", f"{KAC}/flight-status/depart", base,
            ["flightid", ("arrvAirportCode", "arrAirportCode"), "airline", "scheduledatetime"]),
        job("KAC", code, "도착", f"{KAC}/flight-status/arrival", base,
            ["flightid", "airline", "depAirportCode", "scheduledatetime"]),
        job("KAC", code, "국내정보", f"{KAC}/flight-status/info",
            {"schLineType": "D", "schIOType": "O", "schAirCode": code, "schStTime": "0000", "schEdTime": "2359"},
            ["airFln", "std", "etd", "gate", "rmkKor"]),
        job("KAC", code, "국제정보", f"{KAC}/flight-status/info",
            {"schLineType": "I", "schIOType": "O", "schAirCode": code, "schStTime": "0000", "schEdTime": "2359"},
            ["airFln", "std", "etd", "gate", "rmkKor"]),
    ]


def keys_summary(parsed):
    """응답 항목 이름과 첫 건 전체를 보여 준다 (필드 이름이 문서에 없는 API 확인용)."""
    if not parsed or not parsed["items"]:
        return None
    first = parsed["items"][0]
    return "항목: " + ", ".join(first.keys()) + "\n       첫 건 전체: " + ", ".join(f"{k}={v}" for k, v in first.items())


def kac_single_jobs():
    ymd = datetime.now(KST).strftime("%Y%m%d")
    return [
        # 새로 추천: 전국공항 시간대별 탑승승객 수 (활용신청 필요, 개발계정 자동승인)
        job("KAC", "-", "시간대별 승객", f"{KAC}/daily-expect-passenger",
            {"schStDate": ymd, "schEdDate": ymd, "schAirport": "PUS", "numOfRows": 30, "pageNo": 1},
            ["__field_names_unknown__"], summary=keys_summary,
            label="전국공항 시간대별 탑승승객 수 (daily-expect-passenger, 김해)"),
        job("KAC", "-", "시간대별 승객/info", f"{KAC}/daily-expect-passenger/info",
            {"schStDate": ymd, "schEdDate": ymd, "schAirport": "PUS", "numOfRows": 30, "pageNo": 1},
            ["__field_names_unknown__"], summary=keys_summary,
            label="전국공항 시간대별 탑승승객 수 (daily-expect-passenger/info, 김해)"),
        job("KAC", "-", "시간대별 승객/list", f"{KAC}/daily-expect-passenger/list",
            {"schStDate": ymd, "schEdDate": ymd, "schAirport": "PUS", "numOfRows": 30, "pageNo": 1},
            ["__field_names_unknown__"], summary=keys_summary,
            label="전국공항 시간대별 탑승승객 수 (daily-expect-passenger/list, 김해)"),

        # 매장: 전국공항 임대계약 정보 (공항 코드로 거를 수 있음). 예전 airport-retail-contract 는 04 로 실패
        job("KAC", "GMP", "매장(임대계약)", f"{KAC}/airport-lease-contract/info",
            {"schAirportCode": "GMP", "contractStatus": "ON", "numOfRows": 5, "pageNo": 1},
            ["storeName", "typeOfBusiness", "leaseLocation", "airportCode", "contractEndDate"],
            variants=[{"contractStatus": None}], label="전국공항 임대계약 정보 (airport-lease-contract, 김포)"),
        job("KAC", "-", "상업시설(예전)", f"{KAC}/airport-retail-contract/info",
            {"pageNo": 1, "numOfRows": 100}, ["Name1", "Sangho", "Type4Text", "Gtext", "ContDate"],
            summary=commercial_summary, label="공항 상업시설 임대계약 (airport-retail-contract, 앱에서는 더 쓰지 않음)"),
        job("KAC", "-", "실시간 상세", f"{KAC}/flight-status/detail",
            {"numOfRows": 5, "pageNo": 1}, ["AIR_FLN", "AIRPORT", "STD", "ETD", "FLIGHT_DATE", "IO", "GATE", "RMK_KOR"],
            summary=detail_summary, label="실시간 항공운항 현황 상세 (flight-status/detail)"),
    ]


# ---------- 실행 ----------

def run_job(j):
    raw = call(j["url"], j["params"])
    status, detail, parsed = judge(j["fields"], raw)
    if status == "NO_RESULT":
        for ov in j["variants"]:
            st2, d2, p2 = judge(j["fields"], call(j["url"], merged(j["params"], ov)))
            if st2 in ("OK", "CHECK"):
                status, parsed = st2, p2
                detail = f"{d2} · 기본 조건은 0건, 변경 조건({describe(ov)})에서 수신 → 앱 파라미터를 이 조건으로 맞추세요"
                break
    res = {"group": j["group"], "airport": j["airport"], "service": j["service"], "label": j["label"],
           "status": status, "detail": detail, "preview": None, "summary": None}
    if parsed and parsed["items"] and status in ("OK", "CHECK"):
        res["preview"] = preview(j["fields"], parsed["items"][0])
        if j["summary"]:
            res["summary"] = j["summary"](parsed)
    return res


def run_congestion(ver, codes):
    fields = ["IATA_APCD", "PRC_HR", "CGDR_ALL_LVL"]
    status, detail, parsed = judge(fields, call(f"{KAC}/airport-congestion/{ver}",
                                                {"pageNo": 1, "numOfRows": 100}))
    seen = sorted({str(get(i, "IATA_APCD")) for i in parsed["items"]}) if parsed and parsed["items"] else []
    out = []
    for code in codes:
        if status in ("OK", "CHECK") and code in seen:
            st, d = status, f"{ver} 응답에 {code} 있음 · {detail}"
        elif status in ("OK", "CHECK"):
            st, d = "NO_RESULT", f"{ver} 응답에 {code} 없음 · 응답에 있는 공항: {', '.join(seen) or '없음'}"
        else:
            st, d = status, detail
        first = next((i for i in (parsed or {}).get("items") or [] if str(get(i, "IATA_APCD")) == code), None)
        summary = ("한 시점 값: " + ", ".join(f"{k}={v}" for k, v in first.items())) if first else None
        out.append({"group": "KAC", "airport": code, "service": "혼잡도", "label": f"혼잡도 {code} ({ver})",
                    "status": st, "detail": d, "preview": None, "summary": summary})
    return out


def show(r, compact):
    if compact:
        print(f"[{r['status']}] {r['airport']} {r['service']} — {r['detail']}")
        if r.get("summary"):
            print(f"       {r['summary']}")
        return
    print(f"[{r['status']}] {r['label']}")
    print(f"       {r['detail']}")
    if r["preview"]:
        print(f"       첫 건: {r['preview']}")
    if r["summary"]:
        print(f"       {r['summary']}")
    print()


def print_matrix(results, sel):
    cols = ["출발", "도착", "국내정보", "국제정보", "혼잡도"]
    short = {"OK": "OK", "NO_RESULT": "NO", "CHECK": "CHK", "FAIL": "FAIL"}
    table = {(r["airport"], r["service"]): short[r["status"]] for r in results if r["group"] == "KAC"}
    print("\n공항별 요약 (OK 수신 · NO 0건 · CHK 항목명 다름 · FAIL 실패 · - 대상 아님)")
    print("공항    " + "  ".join(f"{c:<8}" for c in cols))
    for code in sel:
        cells = [table.get((code, c), "-") for c in cols]
        print(f"{code:<6}  " + "  ".join(f"{x:<8}" for x in cells))


def print_summary(results):
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\n요약:", "  ".join(f"{k}={v}" for k, v in counts.items()))
    fails = [r for r in results if r["status"] == "FAIL"]
    checks = [r for r in results if r["status"] == "CHECK"]
    if fails:
        print("\nFAIL (서비스별 활용신청·승인, 호출 주소, 일일 한도 확인. 원문은 위 줄에 있음):")
        for r in fails:
            print(f"  - {r['label']} [{r['airport']}]: {r['detail']}")
    if checks:
        print("\nCHECK (명세 항목명과 실제 응답이 다름 → 실제 항목명으로 파서 수정 필요):")
        for r in checks:
            print(f"  - {r['label']} [{r['airport']}]: {r['detail']}")
    ok_airports = sorted({r["airport"] for r in results
                          if r["group"] == "KAC" and r["service"] == "출발" and r["status"] == "OK"})
    print(f"\n출발 자료가 확인된 한국공항공사 공항: {', '.join(ok_airports) or '없음'}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="공공데이터 API 수신 확인")
    ap.add_argument("--group", choices=["all", "incheon", "kac"], default="all")
    ap.add_argument("--airport", nargs="*", default=None, help="한국공항공사 공항 코드 (예: GMP CJU)")
    args = ap.parse_args()

    if not KEY:
        print("DATA_GO_KR_KEY 가 없습니다. .env.example 을 .env 로 복사한 뒤 키를 채우세요.")
        return 2
    sel = [a.upper() for a in args.airport] if args.airport else KAC_AIRPORTS
    bad = [a for a in sel if a not in KAC_AIRPORTS]
    if bad:
        print("한국공항공사 공항 코드가 아닙니다:", ", ".join(bad))
        return 2

    ymd = datetime.now(KST).strftime("%Y%m%d")
    print(f"기준일(KST): {datetime.now(KST).date().isoformat()}  ·  대상: {args.group}\n")
    results = []

    if args.group in ("all", "incheon"):
        print("== 인천공항 (인천국제공항공사) ==")
        for j in incheon_jobs(ymd):
            r = run_job(j)
            results.append(r)
            show(r, compact=False)

    if args.group in ("all", "kac"):
        print("== 한국공항공사 ==")
        print("-- 공항 혼잡도 (v1·v2, 공항별로 확인) --")
        for ver, codes in CONGESTION.items():
            wanted = [c for c in codes if c in sel]
            if wanted:
                for r in run_congestion(ver, wanted):
                    results.append(r)
                    show(r, compact=True)
        print("-- 공항별 실시간 운항 (출발·도착·국내정보·국제정보) --")
        for code in sel:
            for j in kac_airport_jobs(code, ymd):
                r = run_job(j)
                results.append(r)
                show(r, compact=True)
        print("-- 공항 단위가 아닌 서비스 --")
        for j in kac_single_jobs():
            r = run_job(j)
            results.append(r)
            show(r, compact=False)
        print_matrix(results, sel)

    print_summary(results)
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
