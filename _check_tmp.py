import sys
sys.path.insert(0, ".")
from app.agent import llm
for t in ["지금 대구로 도착하는 항공편 알려줘", "김해에서 오는 도착편 알려줘", "인천 도착하는 나리타발 항공편"]:
    it = llm.rule_intent(t)
    print(t, "->", it.intent, "airport=", it.airport, "origin=", it.origin, "dest=", it.destination)
