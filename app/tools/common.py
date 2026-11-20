"""공공데이터포털 호출 공통부 (명세 4-1 · 4-2).

- 키는 config 에서만 읽고, 오류 문구에 섞여도 화면·로그에 남기지 않도록 가린다 (NF3001, NF3002).
- 포털 본문 오류(Unauthorized 등)와 제공기관 resultCode 를 구분해 ApiError 로 올린다.
"""
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, quote_plus

import requests

from app import config

PORTAL_ERRORS = {
    "Unauthorized": "인증키가 없거나 유효하지 않음 (.env 의 DATA_GO_KR_KEY 확인)",
    "Forbidden": "활용신청 내역이 없거나 승인 전 (포털 마이페이지에서 승인 상태 확인)",
    "API not found": "호출 주소가 틀렸거나 폐기된 API",
    "API rate limit exceeded": "동시 호출 수 초과 — 잠시 후 다시 호출",
    "API token quota exceeded": "일일 호출 허용량 초과 — 다음 날 초기화",
}
# 게이트웨이 반환 코드 (공공데이터포털 공통). 화면에 뜻을 함께 보여 준다.
GATEWAY_CODES = {
    "01": "제공기관 서비스 오류", "02": "제공기관 DB 오류", "03": "자료 없음",
    "04": "제공기관 서버 HTTP 오류 (제공기관 쪽 장애)", "05": "제공기관 서버 연결 실패",
    "10": "잘못된 요청 변수", "11": "필수 요청 변수 누락", "12": "폐기되었거나 없는 서비스",
    "20": "서비스 접근 거부 (활용신청 확인)", "22": "하루 호출 한도 초과", "30": "등록되지 않은 인증키",
    "31": "기한이 지난 인증키", "32": "등록되지 않은 IP",
}
PARAM = re.compile(r"(?i)(serviceKey)=([^&\s'\"]+)")
_cache = {}


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def ok(data):
    return {"ok": True, "data": data}


def fail(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def scrub(text):
    text = str(text)
    key = config.DATA_GO_KR_KEY
    if key:
        text = text.replace(key, "***").replace(quote(key, safe=""), "***")
    return PARAM.sub(lambda m: f"{m.group(1)}=***", text)


def parse(content, text):
    """JSON 과 XML 을 같은 모양({code, msg, total, items})으로 맞춘다."""
    if text.startswith("{"):
        data = json.loads(text)
        gw = data.get("OpenAPI_ServiceResponse")
        if isinstance(gw, dict):                        # 게이트웨이 오류 (JSON)
            head = gw.get("cmmMsgHeader", gw) or {}
            return {"code": str(head.get("returnReasonCode") or "ERR"),
                    "msg": head.get("returnAuthMsg") or head.get("errMsg") or "", "total": None, "items": []}
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
        return {"code": str(header.get("resultCode", "")), "msg": header.get("resultMsg", ""),
                "total": body.get("totalCount"), "items": items}
    root = ET.fromstring(content)
    if root.tag == "OpenAPI_ServiceResponse":           # 게이트웨이 오류 (XML)
        return {"code": root.findtext(".//returnReasonCode") or "ERR",
                "msg": root.findtext(".//returnAuthMsg") or "", "total": None, "items": []}
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return {"code": root.findtext(".//resultCode") or "", "msg": root.findtext(".//resultMsg") or "",
            "total": root.findtext(".//totalCount"), "items": items}


def fetch(url, params, cache=True, ttl=None):
    """공공데이터 한 번 호출. 같은 요청은 CACHE_TTL_SEC 동안 재사용한다 (일일 한도 보호)."""
    if not config.DATA_GO_KR_KEY:
        raise ApiError("NO_KEY", "DATA_GO_KR_KEY 가 설정되지 않았습니다. .env 를 확인하십시오.")
    cache_key = (url, tuple(sorted(params.items())))
    hit = _cache.get(cache_key)
    if cache and hit and time.time() - hit[0] < (ttl or config.CACHE_TTL_SEC):
        return hit[1]
    query = {"serviceKey": config.DATA_GO_KR_KEY, "type": "json", **params}
    try:
        r = requests.get(url, params=query, timeout=config.HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise ApiError("NETWORK", f"요청 실패: {scrub(e)}")
    text = r.content.decode("utf-8", "replace").strip()
    if r.status_code != 200:
        raise ApiError("HTTP", f"HTTP {r.status_code} · {PORTAL_ERRORS.get(text, scrub(text[:80]))}")
    if not text.startswith(("{", "<")):
        raise ApiError("PORTAL", PORTAL_ERRORS.get(text, f"예상 밖 응답: {scrub(text[:80])}"))
    try:
        parsed = parse(r.content, text)
    except Exception as e:
        raise ApiError("PARSE", f"응답 해석 실패: {scrub(e)}")
    if parsed["code"] != "00":
        meaning = GATEWAY_CODES.get(parsed["code"], "")
        detail = " · ".join(x for x in (meaning, parsed["msg"]) if x)
        raise ApiError("PROVIDER", f"제공기관 결과코드 {parsed['code'] or '없음'}" + (f" ({detail})" if detail else ""))
    if cache:
        _cache[cache_key] = (time.time(), parsed)
    return parsed


def flight_no_key(text):
    """편명을 비교용 한 가지 모양으로 만든다. 공백과 대소문자는 무시하고, 숫자 앞의 0은 뺀다 (KE0123 → KE123)."""
    s = re.sub(r"\s", "", str(text or "")).upper()
    m = re.fullmatch(r"([A-Z0-9]{2})(\d+)", s)
    return f"{m.group(1)}{int(m.group(2))}" if m else s


def pick(item, *names):
    """필드 이름을 대소문자 무시로 찾는다. 명세와 실제 응답의 표기 차이(estimatedDatetime 등)를 흡수한다."""
    lowered = {k.lower(): v for k, v in item.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def to_dt(value):
    """'2026-11-19 13:05', '202611191305' 등을 'YYYY-MM-DD HH:MM' 으로 맞춘다. 해석 불가면 None."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 12:
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}"


def maps_link(query):
    """키가 없어도 동작하는 구글 지도 검색 링크 (기획 F3015 기본 방식)."""
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"
