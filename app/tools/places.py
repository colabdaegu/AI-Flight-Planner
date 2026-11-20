"""공항 검색 (로컬 서버 도구). 이 서버가 가진 공항 목록에서 이름·코드로 찾는다. 외부 API 를 부르지 않는다."""
from app import airports
from app.tools.registry import Tool, register


def search_airports(query, scope="all"):
    if scope not in ("all", "origin"):
        raise ValueError("scope 는 all 또는 origin 이어야 합니다.")
    rows = airports.search(query, scope=scope)
    return {"query": query, "count": len(rows), "rows": rows}


register(Tool(
    name="search_airports",
    kind="local",
    description="도시·공항 이름(한글·영문)이나 3글자 코드를 공항 코드로 바꿀 때 쓴다. scope=origin 이면 출발 자료가 있는 국내 공항만.",
    parameters={"type": "object", "properties": {
        "query": {"type": "string"}, "scope": {"type": "string", "enum": ["all", "origin"]}},
        "required": ["query"]},
    func=search_airports))
