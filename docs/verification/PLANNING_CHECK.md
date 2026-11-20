# 기획 검증 기록 (명세 2.7)

실행 2026-11-20 15:49 · `python verify/run_checks.py` 가 함께 만든 기록입니다.

## 1. 외부 API 호출 확인

`python api_check/check_apis.py --group all --airport GMP CJU` 출력 (키는 가림):

- 판정 수(출력 안 단어 수 기준): OK 31 · NO_RESULT 0 · CHECK 3 · FAIL 6
- 전체 출력: [api_check.txt](api_check.txt)

## 2. LLM 연결 · Tool 명세를 넘겼을 때 Tool 선택

- LLM 호출 성공 (gpt-5.4-nano, 1105ms): 연결됨

| 요청 | LLM 이 고른 Tool (인자) |
|---|---|
| 김포에서 제주 가는 가장 빠른 편 알려줘 | search_departures({"airport": "GMP", "destination": "CJU"}) |
| KE1205 지금 탑승구 어디야? | search_flight({"flight_no": "KE1205", "date": "20261120"}) |
| 인천공항 출국장 약국 어디 있어? | get_incheon_facilities({"keyword": "약국", "area": "D", "category": "약국", "limit": 20}) |
| 안녕! | Tool 없이 답함 |

## 3. 설계 시험 (대표 요청을 단계별로 따라가기)

### 내일 인천공항에 9시 도착하는데 탈 수 있는 오사카행 항공편

- 분석(기준·선호·제약): `{"기준": {"date": "20261121", "airport": "ICN"}, "선호": {"destination": "오사카"}, "제약": {"arrive_airport_at": "0900"}}`
- 비워 둔 조건: flexible_date, from_time, flight_no, origin, earliest, keyword, to_time, buffer_min
- 계획(시도 0): search_departures{"airport": "ICN", "date": "20261121"} → get_incheon_passenger_forecast{"selectdate": "1"}
- 판단: 후보 24 · 제외 16 {'준비 시간 부족': 16} · 선택 KE727

### 김포에서 제주 가는 가장 빠른 편, 23시 59분 이후로

- 분석(기준·선호·제약): `{"기준": {"from_time": "2359", "airport": "GMP"}, "선호": {"destination": "제주", "origin": "김포", "earliest": true}, "제약": {"to_time": "2359"}}`
- 비워 둔 조건: date, flexible_date, flight_no, keyword, arrive_airport_at, buffer_min
- 계획(시도 0): search_departures{"airport": "GMP", "date": "20261120", "from_time": "2359", "to_time": "2359", "destination": "CJU"} → get_airport_congestion{"airport": "GMP"}
- 재계획 1: 조건을 만족하는 편이 없음 → 시간 조건을 풀고 같은 날 전체를 다시 조회
- 계획(시도 1): search_departures{"airport": "GMP", "date": "20261120", "destination": "CJU"} → get_airport_congestion{"airport": "GMP"}
- 판단: 후보 34 · 제외 0  · 선택 KE1143
- 앞 단계 결과 → 다음 입력: get_flight_detail{"airport": "GMP", "flight_no": "KE1143", "date": "20261120", "direction": "departure"}

### 빠른 시일 내에 김포에서 센다이 가는 편

- 분석(기준·선호·제약): `{"기준": {"flexible_date": true, "airport": "GMP"}, "선호": {"destination": "센다이", "origin": "김포", "earliest": true}, "제약": {}}`
- 비워 둔 조건: date, from_time, flight_no, keyword, to_time, arrive_airport_at, buffer_min
- 계획(시도 0): search_departures{"airport": "GMP", "date": "20261120", "destination": "SDJ"} → get_airport_congestion{"airport": "GMP"}
- 재계획 1: 조건을 만족하는 편이 없음 → 1일 뒤 날짜로 넓혀 다시 조회
- 계획(시도 1): search_departures{"airport": "GMP", "date": "20261121", "destination": "SDJ"}
- 재계획 2: 조건을 만족하는 편이 없음 → 2일 뒤 날짜로 넓혀 다시 조회
- 계획(시도 2): search_departures{"airport": "GMP", "date": "20261122", "destination": "SDJ"}
- 재계획 3: 조건을 만족하는 편이 없음 → 3일 뒤 날짜로 넓혀 다시 조회
- 계획(시도 3): search_departures{"airport": "GMP", "date": "20261123", "destination": "SDJ"}
- 판단: 후보 0 · 제외 0  · 선택 -

