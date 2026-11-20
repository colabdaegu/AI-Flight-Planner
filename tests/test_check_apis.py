"""확인 프로그램의 판정 로직 테스트. 실제 호출은 하지 않고 call 을 가짜로 바꾼다."""
import json

from api_check import check_apis as ca


def body(items, code="00"):
    node = {"item": items} if items else ""
    return json.dumps({"response": {"header": {"resultCode": code, "resultMsg": "NORMAL"},
                                    "body": {"totalCount": len(items), "items": node}}}).encode()


def test_missing_result_code_is_fail_with_snippet():
    st, detail, _ = ca.judge(["x"], {"http": 200, "content": b'{"foo": 1}'})
    assert st == "FAIL"
    assert "결과코드 없음" in detail and "foo" in detail


def test_different_item_names_is_check_and_lists_real_names():
    st, detail, _ = ca.judge(["needed"], {"http": 200, "content": body([{"other": "1"}])})
    assert st == "CHECK"
    assert "needed" in detail and "other" in detail


def test_zero_rows_is_no_result_not_fail():
    st, _, _ = ca.judge(["x"], {"http": 200, "content": body([])})
    assert st == "NO_RESULT"


def test_provider_error_code_is_fail():
    st, detail, _ = ca.judge(["x"], {"http": 200, "content": body([], code="11")})
    assert st == "FAIL" and "11" in detail


def test_variant_recovers_zero_rows(monkeypatch):
    def fake_call(url, params):
        if "b" in params:
            return {"http": 200, "content": body([])}
        return {"http": 200, "content": body([{"x": "1"}])}

    monkeypatch.setattr(ca, "call", fake_call)
    j = ca.job("인천", "ICN", "출발", "u", {"a": "1", "b": "2"}, ["x"], variants=[{"b": None}])
    r = ca.run_job(j)
    assert r["status"] == "OK"
    assert "변경 조건" in r["detail"]


def test_congestion_reports_each_airport(monkeypatch):
    rows = [{"IATA_APCD": "GMP", "PRC_HR": "13:00", "CGDR_ALL_LVL": "1.0"}]
    monkeypatch.setattr(ca, "call", lambda url, params: {"http": 200, "content": body(rows)})
    res = ca.run_congestion("v1", ["GMP", "CJU"])
    assert {r["airport"]: r["status"] for r in res} == {"GMP": "OK", "CJU": "NO_RESULT"}


def test_key_never_printed(monkeypatch):
    monkeypatch.setattr(ca, "KEY", "SECRETKEY123")
    assert "SECRETKEY123" not in ca.scrub("error serviceKey=SECRETKEY123&x=1 SECRETKEY123")
