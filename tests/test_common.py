"""공통부 테스트: 키 가리기, 포털 오류 구분, JSON/XML 해석. 네트워크 없이 실행된다."""
import json

import pytest

from app import config
from app.tools import common


class FakeResp:
    def __init__(self, status, content):
        self.status_code = status
        self.content = content


def test_scrub_hides_key(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "abc+def%2B")
    text = common.scrub("error serviceKey=abc+def%2B&x=1 and raw abc+def%2B")
    assert "abc" not in text


def test_portal_text_error_is_portal(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "K1")
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: FakeResp(200, b"Unauthorized"))
    with pytest.raises(common.ApiError) as e:
        common.fetch("http://t.test/portal", {})
    assert e.value.code == "PORTAL"


def test_provider_error_code(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "K2")
    body = json.dumps({"response": {"header": {"resultCode": "11", "resultMsg": "NODATA"}}}).encode()
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: FakeResp(200, body))
    with pytest.raises(common.ApiError) as e:
        common.fetch("http://t.test/provider", {})
    assert e.value.code == "PROVIDER"


def test_json_items_as_dict_becomes_list(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "K3")
    body = json.dumps({"response": {"header": {"resultCode": "00"},
                                    "body": {"totalCount": 1, "items": {"item": {"flightId": "KE1"}}}}}).encode()
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: FakeResp(200, body))
    parsed = common.fetch("http://t.test/json-dict", {})
    assert parsed["items"] == [{"flightId": "KE1"}]


def test_xml_items_parsed(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "K4")
    xml = (b"<response><header><resultCode>00</resultCode></header><body><totalCount>1</totalCount>"
           b"<items><item><adate>2026-11-20</adate></item></items></body></response>")
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: FakeResp(200, xml))
    parsed = common.fetch("http://t.test/xml", {})
    assert parsed["items"][0]["adate"] == "2026-11-20"


def test_gateway_xml_error(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "K5")
    xml = (b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode>"
           b"<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader>"
           b"</OpenAPI_ServiceResponse>")
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: FakeResp(200, xml))
    with pytest.raises(common.ApiError) as e:
        common.fetch("http://t.test/gateway", {})
    assert e.value.code == "PROVIDER"


def test_no_key_is_reported_without_calling(monkeypatch):
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "")
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: pytest.fail("호출하면 안 됨"))
    with pytest.raises(common.ApiError) as e:
        common.fetch("http://t.test/nokey", {})
    assert e.value.code == "NO_KEY"


def test_to_dt_formats():
    assert common.to_dt("2026-11-19 13:05") == "2026-11-19 13:05"
    assert common.to_dt("202611191305") == "2026-11-19 13:05"
    assert common.to_dt("0905") is None


def test_flight_no_key_ignores_leading_zero_and_spaces():
    from app.tools.common import flight_no_key
    assert flight_no_key("KE0123") == flight_no_key("ke 123") == "KE123"
    assert flight_no_key("KE1041") == "KE1041"
    assert flight_no_key("7C0301") == "7C301"
