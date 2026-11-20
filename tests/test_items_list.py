"""items 가 목록을 바로 담는 JSON 응답(인천 여객기 등)을 두 파서가 모두 읽는지 확인한다.

이 버그 때문에 인천 자료가 전부 0건으로 보였다 (실제 응답에는 totalCount 1157 이 있었다).
"""
import json

from api_check import check_apis as ca
from app.tools import common

LIST_STYLE = json.dumps({"response": {
    "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
    "body": {"numOfRows": 2, "pageNo": 1, "totalCount": 1157,
             "items": [{"flightId": "LJ5264", "gatenumber": "211"},
                       {"flightId": "TW250", "gatenumber": "105"}]}}}, ensure_ascii=False)

ITEM_STYLE = json.dumps({"response": {
    "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
    "body": {"totalCount": 1, "items": {"item": {"flightid": "RS6347"}}}}})


def test_check_apis_reads_list_style_items():
    parsed = ca.parse(LIST_STYLE.encode("utf-8"), LIST_STYLE)
    assert parsed["code"] == "00"
    assert [i["flightId"] for i in parsed["items"]] == ["LJ5264", "TW250"]
    assert parsed["total"] == 1157


def test_app_common_reads_list_style_items():
    parsed = common.parse(LIST_STYLE.encode("utf-8"), LIST_STYLE)
    assert len(parsed["items"]) == 2


def test_item_style_still_works_in_both():
    assert ca.parse(ITEM_STYLE.encode(), ITEM_STYLE)["items"][0]["flightid"] == "RS6347"
    assert common.parse(ITEM_STYLE.encode(), ITEM_STYLE)["items"][0]["flightid"] == "RS6347"


def test_empty_items_string_is_zero_rows():
    empty = json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": ""}}})
    assert ca.parse(empty.encode(), empty)["items"] == []
    assert common.parse(empty.encode(), empty)["items"] == []
