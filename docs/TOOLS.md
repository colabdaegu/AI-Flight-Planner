# Tool 정의서

이 문서는 `app/tools/registry.py` 에 실제로 등록된 도구 목록(`GET /api/tools`)에서 뽑았습니다. 도구를 추가하면 `python docs/make_tools_doc.py` 로 다시 만들 수 있습니다.

## 공통 규칙 (F3001)

- 모든 도구는 `Tool(name, description, parameters, func, kind)` 로 등록합니다. 에이전트에는 이름·설명·입력 스키마만 넘깁니다.
- `kind`: `external` = 외부 API(공공데이터포털)를 감싼 도구, `local` = 이 서버의 기능(공항 목록 검색, 여러 도구 묶음)을 감싼 도구.
- 결과는 항상 같은 봉투입니다. 성공 `{"ok": true, "data": {...}}`, 실패 `{"ok": false, "error": {"code", "message"}}`. 예외를 밖으로 던지지 않습니다 (NF3003).
- 같은 요청은 `CACHE_TTL_SEC`(300초) 동안 재사용해 하루 호출 한도를 보호합니다. 시설 안내는 6시간.

### 실패 코드

| 코드 | 뜻 | 에이전트 처리 |
|---|---|---|
| NO_KEY | `.env` 에 DATA_GO_KR_KEY 없음 | 재시도하지 않고 설정 안내 |
| BAD_ARGS | 잘못된 입력(공항 코드, 날짜 형식 등) | 재시도하지 않고 안내 |
| NETWORK | 요청 실패(연결·시간 초과) | 같은 조건으로 1회 재시도 |
| HTTP / PORTAL | 공공데이터포털 오류 응답(인증·한도 초과 등) | 1회 재시도 후 안내 |
| PROVIDER | 제공기관 결과 코드가 00 이 아님 | 1회 재시도 후 안내 |
| PARSE | 응답 해석 실패 | 1회 재시도 후 안내 |
| INTERNAL | 내부 오류 (세부 내용은 화면에 내보내지 않음) | 안내 |

### 결과가 없을 때

운항 목록이 0건이면 오류가 아니라 `count: 0` 과 `note`("조건에 맞는 운항은 0건")로 돌려줍니다. 받은 건수가 전체(total)보다 적으면 `note` 에 그 사실을 적습니다. 판단에 필요한 값(탑승구, 예상 시각 등)이 비어 있으면 임의로 채우지 않고 `null` 로 둡니다 (F3003).

## 도구 목록

### `search_departures` — 후보 조회 (F3002)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 공항의 출발 운항 시간표 조회. 인천(ICN)은 인천공항공사, 그 외는 한국공항공사 자료. 날짜(YYYYMMDD)를 생략하면 오늘. 목적지 IATA 코드로 거를 수 있다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| airport | string | 예 | 출발 공항 IATA 코드 (ICN, GMP, CJU, PUS 등) |
| date | string |  | YYYYMMDD |
| from_time | string |  | HHMM |
| to_time | string |  | HHMM |
| destination | string |  | 도착 공항 IATA 코드 |

### `search_arrivals` — 후보 조회 (F3002)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 공항의 도착 운항 시간표 조회. 날짜(YYYYMMDD)를 생략하면 오늘. 출발지 IATA 코드로 거를 수 있다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| airport | string | 예 | 도착 공항 IATA 코드 (ICN, GMP 등) |
| date | string |  | YYYYMMDD |
| from_time | string |  | HHMM |
| to_time | string |  | HHMM |
| origin | string |  | 출발 공항 IATA 코드 |

### `search_flight` — 후보 조회 · 편명

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 편명으로 운항 찾기. 선택한 공항의 하루 출발·도착 목록에서 같은 편명을 찾는다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| flight_no | string | 예 | 예: KE123 |
| airport | string |  | 기준 공항 IATA 코드 (기본 ICN) |
| date | string |  | YYYYMMDD |

### `get_flight_detail` — 상세정보 조회 (F3003)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 후보 항공편의 편명으로 상세(탑승구·터미널·상태·예상 시각)를 확인할 때 쓴다. 출발 목록에 탑승구가 없을 때(한국공항공사 공항) 특히 필요하다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| airport | string | 예 | 기준 공항 IATA 코드 |
| flight_no | string | 예 | 예: KE123 |
| date | string |  | YYYYMMDD |
| direction | string (departure / arrival) |  |  |

### `get_commercial_facilities` — 보조 정보 (F3004)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 공항 상업시설(면세·편의 매장) 임대계약 목록. 키워드 포함 검색, 기본은 계약 유효 건만.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| keyword | string |  |  |
| active_only | boolean |  |  |

### `get_airport_congestion` — 보조 정보 (F3004)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 공항 시간대별 혼잡도 조회 (김포·제주·김해·청주·대구). 참고용.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| airport | string | 예 | IATA 코드 |

### `get_incheon_facilities` — 보조 정보 (F3004)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 인천공항의 면세점·식당·카페·약국·환전·라운지·편의시설 위치와 운영 시간을 찾을 때 쓴다. keyword(예: 면세, 약국), area(D=출국장, A=입국장), category 로 거를 수 있다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| keyword | string |  |  |
| area | string (D / A) |  |  |
| category | string |  |  |
| limit | integer |  |  |

### `get_incheon_passenger_forecast` — 보조 정보 (F3004)

- 종류: 외부 API 도구
- 설명(에이전트가 보는 문장): 인천공항 터미널별 출국·입국 예상 승객 수(시간대별)를 볼 때 쓴다. 혼잡도 API 가 없는 인천의 혼잡 참고 자료. selectdate 0=오늘, 1=내일.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| selectdate | string (0 / 1) |  |  |

### `search_airports` — 입력 보조

- 종류: 로컬 서버 도구
- 설명(에이전트가 보는 문장): 도시·공항 이름(한글·영문)이나 3글자 코드를 공항 코드로 바꿀 때 쓴다. scope=origin 이면 출발 자료가 있는 국내 공항만.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| query | string | 예 |  |
| scope | string (all / origin) |  |  |

### `get_airport_overview` — 화면용 묶음

- 종류: 로컬 서버 도구
- 설명(에이전트가 보는 문장): 공항 시설 화면용. 공항 하나의 혼잡·시설 자료를 한 번에 모을 때 쓴다 (여러 외부 도구를 묶은 로컬 도구).

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| airport | string | 예 | IATA 코드 |

### `get_hourly_traffic` — 보조 정보 (F3004)

- 종류: 외부 API 도구
- 설명: 한국공항공사 공항 14곳의 시간대별 이용 흐름. 1순위 '전국공항 시간대별 탑승승객 수'(daily-expect-passenger), 쓸 수 없으면 실시간 운항 정보로 시간대별 출발·도착 편수를 센다. `source`(expected_passengers / flights)와 `fallback_reason` 으로 어느 쪽을 썼는지 밝힌다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| airport | string | 예 | 한국공항공사 공항 IATA 코드 |
| date | string |  | YYYYMMDD |

> `get_airport_congestion` 은 시간대별 기록이 아니라 **지금 한 시점**의 국내선 구간별 단계(1구간 체크인→신분확인, 2구간 신분확인→보안검색, 3구간 보안검색→탑승)만 준다. 그래서 공항마다 1건이 정상이다.

## 출력 구조 (주요 필드)

- 운항 목록(`search_departures`, `search_arrivals`, `search_flight`): `airport, airport_name, date, provider, count, total, note, flights[]`.
  `flights[]` 한 줄: `provider, airport, direction(departure|arrival), airline, flight_no, scheduled, estimated, remark, terminal, gate, destination_code/destination_name`(출발) 또는 `origin_code/origin_name`(도착). 시각은 `YYYY-MM-DD HH:MM`.
- 편명 상세(`get_flight_detail`): `airport, flight_no, date, direction, count, rows[], source, note`.
- 혼잡도(`get_airport_congestion`): `rows[{time, level, level_a, level_b, level_c, extra}], segments` — 한 시점 값, 단계는 제공기관 그대로.
- 시간대별 흐름(`get_hourly_traffic`): `source, unit(명/편), rows[{hour, departure, arrival, value}], note, fallback_reason`.
- 운항 목록의 `codeshares`: 같은 비행기의 공동운항 편명. 대표 편 한 줄로 묶는다.
- 승객 예고(`get_incheon_passenger_forecast`): `rows[{hour, t1_departure, t1_arrival, t2_departure, t2_arrival, ratio}], peak, current_hour, note`.
- 시설 안내(`get_incheon_facilities`): `count, total, rows[{name, items, location, hours, tel, area, area_name, category, terminal, floor}], summary{by_category, by_area, by_place}`.
- 공항 개요(`get_airport_overview`): `airport, airport_name, has_congestion, has_passenger, parts{...}` — 각 부분은 위 도구의 봉투 그대로(부분 실패 허용).

## 도구를 추가하는 방법

1. `app/tools/` 에 함수를 만들고 `register(Tool(name=..., description="언제 쓰는지", parameters={JSON 스키마}, func=..., kind=...))` 로 등록합니다.
2. `app/tools/__init__.py` 의 import 목록에 모듈을 추가합니다.
3. 에이전트가 쓰게 하려면 `app/agent/nodes.py` 의 `plan` 에서 의도에 맞게 단계를 넣고, `respond` 에 출력 문장을 추가합니다.
4. `tests/` 에 가짜 응답으로 정상·실패 테스트를 추가합니다.
