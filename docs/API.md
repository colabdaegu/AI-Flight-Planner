# API 명세서 (F3017)

기본 주소 `http://127.0.0.1:8000/api`. 화면은 `/` 에서 같은 서버가 제공합니다. 자동 문서는 `/docs`.

모든 응답은 같은 봉투입니다.

```json
{"ok": true, "data": { ... }}
{"ok": false, "error": {"code": "BAD_ARGS", "message": "사람이 읽을 안내"}}
```

오류 메시지에는 키나 내부 예외 내용을 넣지 않습니다 (NF3002). 상태 코드: 입력 오류 400, 없음 404, 조건 불충족 422, 외부 API 실패 502, 내부 오류 500.

### 요청·응답 모델 (Pydantic, `/docs` 에 스키마로 표시)

| 경로 | 요청 모델 | 응답 모델 (200) | 오류 모델 |
|---|---|---|---|
| `POST /chat` | `ChatIn {message, session_id?, run_id?}` | `ChatOut {ok, data: ChatData}` — ChatData: `session_id, answer, cards, intent, intent_source, replans, trace[Event], audit[AuditBrief], cancelled?` | `ErrorOut {ok:false, error{code, message}}` |
| `POST /plans`, `PUT /plans/{id}` | `PlanIn {title, memo?, airport, flight_no, line, estimated?, dep_manual, buffer_min?, user_arrival?}` | `PlanSavedOut {ok, data{plan: Plan, warnings[]}}` | `ErrorOut` (400·404·422) |
| `GET /plans` · `GET /plans/{id}` | — | `PlanListOut {ok, data[Plan]}` · `PlanOut {ok, data: Plan}` | `ErrorOut` |
| `GET /logs` | — | `LogListOut {ok, data[LogSession]}` | — |
| `GET /logs/{sid}` | — | `LogDetailOut {ok, data{session_id, events[Event]}}` | `ErrorOut` (400·404) |
| `GET /logs/{sid}/audit` | — | `AuditOut` | `ErrorOut` |
| `DELETE /plans/{id}` · `DELETE /logs/{sid}` | — | `DeletedOut {ok, data{deleted}}` | `ErrorOut` |

`Plan` 필드: `id, title, memo, airport, flight_no, flight_date(YYYY-MM-DD), destination, line, scheduled, estimated, checkin_min, checkin_deadline, buffer_min, user_arrival, arrive_by, time_source, arrival_auto, created_at(생성 일시)`.
`Event` 필드: `event, session_id, ts, payload` — 이벤트 종류는 [AGENT.md](AGENT.md) 6장.
모델 정의는 `app/api/routes.py` 에 있고, 서버를 켠 뒤 `http://127.0.0.1:8000/docs` 에서 같은 스키마를 볼 수 있습니다.

## 대화

| 메서드 | 경로 | 요청 | 응답 data |
|---|---|---|---|
| POST | `/chat` | `{"message": "1~500자", "session_id": "선택, 64자 이하 영숫자·-·_", "run_id": "선택, 멈춤 요청용 실행 번호"}` | `session_id, answer, cards, intent, intent_source, replans, trace[], audit` |
| POST | `/chat/cancel` | `{"run_id": "..."}` | `run_id, cancel_requested` — '새 대화'를 누르면 진행 중인 실행을 다음 단계나 다음 도구 호출 전에 멈춤. 멈춘 실행의 `/chat` 응답은 `cancelled: true` |
| DELETE | `/chat/{sid}` | — | `session_id, forgotten` (대화 맥락만 비움, 로그는 유지) |

- `cards`: 추천·후보 항공편 `{airport, airport_name, date, chosen, flights[]}` (없으면 null)
- `trace[]`: 이번 요청의 실행 이벤트 `{event, session_id, ts, payload}` — 화면의 실행 과정
- `audit`: 로깅 에이전트가 이번 실행을 판정한 결과 `{run_index, status(normal/anomaly/undetermined), status_label, warnings[], diagnosis}` (F3020, 로거가 없으면 null)

## 계획 (F3015 · F3016)

요청 본문(PlanIn): `title(필수), memo, airport(필수, IATA), flight_no(필수), line(필수, domestic/international), estimated("YYYY-MM-DD HH:MM", 편명 조회가 안 될 때 쓰는 출발 시각), buffer_min(공항 체크인 시간 0~600분, 비우면 국내선 60·국제선 120), user_arrival(비우면 체크인 마감 시각)`

저장할 때 서버가 편명으로 그날 출발 시각·목적지를 조회한다(인천: 여객기 운항 현황, 그 외: 실시간 운항 정보).
- 체크인 마감 = 항공기 출발 시간(예상 출발 우선) − 공항 체크인 시간 `buffer_min`(입력값, 비우면 국내선 60분 · 국제선 120분). `checkin_min` 에 쓴 분을 돌려줍니다.
- 내 공항 도착 예정이 체크인 마감보다 늦으면 422 `CONDITION_NOT_MET`. 화면의 '여유' = 체크인 마감 − 내 공항 도착 예정(분)
- 예전 방식(항공사 고정 30·60분)으로 저장된 계획도 조회할 때 같은 식으로 다시 계산합니다.
- 고른 노선이 편명 조회 결과(목적지)와 다르면 422 `LINE_MISMATCH`
- 같은 날 준비 구간이 겹치면 저장하고 `warnings` 로 알림

| 메서드 | 경로 | 응답 data |
|---|---|---|
| GET | `/plans/lookup?airport&flight_no&date` | `found, scheduled, estimated, destination, line, checkin_min, checkin_deadline` (입력 중 미리보기) |
| GET | `/plans` | 계획 목록 |
| GET | `/plans/{id}` | 계획 하나 |
| POST | `/plans` | `{plan, warnings[]}` |
| PUT | `/plans/{id}` | `{plan, warnings[]}` |
| DELETE | `/plans/{id}` | `{deleted}` |

계획 한 건: `id, title, memo, airport, flight_no, flight_date, destination, line, scheduled, estimated, checkin_min, checkin_deadline, buffer_min, user_arrival, arrive_by(= 체크인 마감), time_source(flight/manual), created_at`.

## 실행 로그 (F3014) — 수정 API 없음

| 메서드 | 경로 | 응답 data |
|---|---|---|
| GET | `/logs` | `[{session_id, title(첫 질문), kind(chat/screen), started, updated, events, tool_calls, replans, errors, anomalies}]` |
| GET | `/logs/{sid}` | `{session_id, events[]}` (발생 순서) |
| GET | `/logs/{sid}/audit` | `{session_id, runs[], summary{normal, anomaly, undetermined}}` — 실행(질문 1건)마다 이상 징후 판정 (F3020) |
| DELETE | `/logs/{sid}` | `{deleted}` (그 세션의 판정·진단도 함께 지움) |

`runs[]` 한 항목: `{run_index, question, started, judged_at, status, status_label, warnings[{code, label, message, evidence[{seq, event, ts}]}], checks{repeated_call, excessive_replan, unplanned_call}, undetermined[], diagnosis}`.
`diagnosis`는 이상(`anomaly`) 실행에만 붙는 LLM 원인 진단이며 판정과 따로 둡니다: `{status: pending|done|skipped|failed, text 또는 reason, at}`.

## 화면 조회 (도구 직접 호출, 'screen' 세션으로 로그에 남음)

| 메서드 | 경로 | 쿼리 | 도구 |
|---|---|---|---|
| GET | `/departures` | airport, date, from_time, to_time, destination | search_departures |
| GET | `/arrivals` | airport, date, from_time, to_time, origin | search_arrivals |
| GET | `/flight-search` | flight_no, date, airport(선택 — 생략하면 15개 공항 모두) | search_flight |
| GET | `/flights/{airport}/{flight_no}` | date, direction | get_flight_detail |
| GET | `/airport-info/{airport}` | — | get_airport_overview |
| GET | `/facilities` | keyword, area(D/A), category | get_incheon_facilities |
| GET | `/congestion/{airport}` | — | get_airport_congestion |
| GET | `/passenger` | selectdate(0/1) | get_incheon_passenger_forecast |
| GET | `/commercial/{airport}` | keyword, include_business | get_commercial_facilities (전국공항 임대계약) |
| GET | `/airports` | q, scope(all/origin) | 공항 자동 완성 (로컬 목록) |
| GET | `/tools` | — | 등록된 도구 이름·종류·설명·입력 스키마 |
