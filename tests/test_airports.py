"""공항 자동 완성 검색 테스트 (네트워크 불필요)."""
from app import airports


def codes(res):
    return [a["code"] for a in res]


def test_korean_name_finds_domestic():
    assert codes(airports.search("김포"))[0] == "GMP"


def test_english_name_and_code():
    assert codes(airports.search("gimpo"))[0] == "GMP"
    assert codes(airports.search("nrt"))[0] == "NRT"


def test_exact_code_ranks_first():
    assert airports.search("GMP")[0]["code"] == "GMP"


def test_city_name_returns_all_its_airports():
    found = codes(airports.search("도쿄"))
    assert "NRT" in found and "HND" in found


def test_alias_finds_airport():
    assert "PUS" in codes(airports.search("부산"))


def test_spaces_are_ignored():
    assert "JFK" in codes(airports.search("new york"))


def test_origin_scope_excludes_international():
    assert codes(airports.search("도쿄", scope="origin")) == []
    assert "ICN" in codes(airports.search("서울", scope="origin"))


def test_empty_and_unknown_query():
    assert airports.search("") == []
    assert airports.search("zzzz") == []


def test_find_in_text_orders_and_groups_city_airports():
    hits = airports.find_in_text("서울에서 도쿄 가는 편")
    assert [h["code"] for h in hits[:2]] == ["GMP", "ICN"]
    assert {h["code"] for h in hits[2:]} == {"HND", "NRT"}


def test_ambiguous_names_need_context():
    assert airports.find_in_text("세부 일정 알려줘") == []
    assert [h["code"] for h in airports.find_in_text("세부로 가는 비행기")] == ["CEB"]
    assert airports.find_in_text("면세점에서 사가지고 갈래") == []


def test_resolve_codes_for_city_code_and_unknown():
    assert airports.resolve_codes("센다이") == ["SDJ"]
    assert set(airports.resolve_codes("도쿄")) == {"NRT", "HND"}
    assert airports.resolve_codes("sdj") == ["SDJ"]
    assert airports.resolve_codes("아틀란티스") == []
