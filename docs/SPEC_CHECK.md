# 명세 대조표

명세서(AI 코어 프로젝트 3회차) 요구사항마다 어디에 구현했고 무엇으로 확인하는지 정리했습니다.

- **테스트**: `python -m pytest -q` (가짜 응답, 네트워크·키 불필요). 명세 대조 중 보완한 동작은 `tests/test_spec_extra.py`.
- **체크리스트 번호**: `python verify/run_checks.py` 가 실제 API 로 실행하는 셀프 체크리스트 항목. 결과는 [VERIFICATION.md](VERIFICATION.md).

## 기능 요구사항 (필수)

| 번호 | 요구사항 | 구현 위치 | 확인 (테스트 · 체크리스트) |
|---|---|---|---|
| F3001 | Tool 명세 정의·등록, 외부/로컬 구분, 실패 봉투 | `app/tools/registry.py`(Tool.kind, 봉투, `VERIFY_FAIL_TOOLS`), [TOOLS.md](TOOLS.md)(레지스트리에서 생성) | `test_tools_are_marked_external_or_local`, `test_registry_switch_returns_failure_envelope` · 1번 (도구마다 정상·잘못된 입력·키 비움 호출) |
| F3002 | 후보 조회, 기본값, 요청·조회 기준 차이 명시 | `flights.resolve_date`(오늘 기본, 범위 밖은 같은 요일로 추정 + `requested_date`·`date_estimated`·note), `search_departures/arrivals/flight` | `test_date_defaults_…`, `test_future_date_out_of_range_…`, `test_search_reports_requested_and_queried_dates`, `test_bad_date_is_rejected_as_bad_args` · 2번 |
| F3003 | 상세정보 조회, 빈 값은 판단하지 않음 | `get_flight_detail` + 노드 `enrich`, `evaluate_flights`(시각 없는 편 → '판단하지 않음' 제외) | `test_enrich_passes_chosen_flight_to_detail_tool`, `test_flight_without_time_is_not_judged_or_recommended` · 3번 |
| F3004 | 보조 정보(다른 출처) 이름·위치·링크, 결과 없음 형식 | 혼잡도·승객 예고·시설(`map_url`)·매장(`map_url`), 결과 없음 = `count 0 · empty · '결과 없음'` | `test_facilities_give_name_location_link_and_empty_format` · 4번 |
| F3005 | 요청 분석 (기준·선호·제약), 없는 조건은 비움 | `app/agent/llm.py`(LLM·규칙), `nodes.group_conditions` → `intent_analyzed.groups/empty` | `test_conditions_are_grouped_…`, `test_intent_event_carries_groups`, `test_sendai_request_…` · 5번 |
| F3006 | 작업 계획, 불필요 Tool 제외, 그래프 노드 | 노드 `plan`, `graph.STAGES`, `python -m app.agent.graph` | `test_greeting_calls_no_tools`, `test_graph_structure_lists_design_stages` · 6번 |
| F3007 | 호출·결과 통합, 앞 결과 → 다음 입력, 실패해도 진행 | 노드 `execute`, `enrich` | `test_forced_tool_failure_keeps_agent_running`, `test_execute_reuses_results_on_replan` · 7번 (보조 도구를 일부러 실패시킴) |
| F3008 | 제약 판단, 확인 불가 값은 역산·추정 | `evaluate_flights`(공항 도착 + 준비 시간), 응답 '늦어도 공항 도착 HH:MM (… 역산)', '체크인 마감(추정)' | `test_evaluate_applies_airport_arrival_and_buffer`, `test_answer_lists_excluded_flights_with_reason` · 8번 |
| F3009 | 재계획 (결과 재사용, 상한) | 노드 `judge`, `_widen_options`, 캐시(`tool_reused`), `REPLAN_LIMIT=3` | `test_judge_replans_until_cap_then_reports`, `test_full_flow_replans_to_next_day_and_remembers` · 9 · 10번 |
| F3010 | 대화 맥락 (바뀐 조건만 갱신, 원본 응답 미보관) | `app/agent/memory.py`, `merge_context` | `test_followup_…`, 전체 흐름 테스트 2번째 질문 · 11번 |
| F3011 | 추천·보조 정보·판단 기준·근거·제외 사유 | 노드 `respond` ('제외한 후보: 사유 n편(편명 시각)', `judged.rejected_reasons`). 위험 도메인 아님 → 운항 변동 안내 | `test_answer_lists_excluded_flights_with_reason` · 12 · 13번 |
| F3012 | 이벤트 발행 (단계·Tool·LLM 판단 이유), 구독자 무관 | `app/events.py`(구독자 예외 무시, `unsubscribe`), `graph._step`, 각 노드 emit | `test_bus_without_subscriber_and_with_broken_subscriber` · 14 · 15번 |
| F3013 | 로깅 Agent 구독·세션 저장, LLM 미사용, 저장 실패에도 대화 | `app/logger_agent.py`(SQLite events, `VERIFY_LOGGER=off/broken`) | `test_plans_logger.py`, `test_attach_logger_modes` · 14 · 16 · 17번 |
| F3014 | 로그 목록·상세·삭제, 수정 불가 | `GET/DELETE /api/logs…` (수정 경로 없음) | `test_openapi_has_separate_paths_and_no_log_update` · 18번 |
| F3015 | 계획 저장·조회 (날짜·생성 일시), 겹침 알림 | `app/store/plans.py`, `POST /api/plans` → `{plan, warnings}` | `test_plans_logger.py` · 19번 |
| F3016 | 계획 수정·삭제, 화면 반영 | `PUT/DELETE /api/plans/{id}`, 화면은 저장·삭제 뒤 목록을 다시 불러옴 | 20번 (+ 화면 캡처) |
| F3017 | FastAPI, 경로 분리, Pydantic 요청·응답 모델 | `app/api/routes.py`(ChatOut · PlanSavedOut · LogListOut · ErrorOut …), [API.md](API.md) | `test_openapi_…` · 21번 (+ /docs 캡처) |
| F3018 | HTML/CSS/JS 화면, 백엔드 API 사용 | `web/` (AI 플래너 = 첫 화면, 내 계획, 실행 로그 …) | 21번 (+ 네트워크 탭 캡처) |
| F3019 | 실행 단계·Tool 이력·재계획 지점·처리 중·실패·키 누락 안내 | 화면 오른쪽 '실행 과정'(재계획 주황), 처리 중 표시·레이더, 키 누락 배너 | 22번 (+ 화면 캡처 4장) |

## 심화 (선택)

| 번호 | 상태 |
|---|---|
| F3020 로깅 Agent 이상 징후 판단 | **구현.** `app/audit.py`, `app/logger_agent.py`, `app/store/audits.py`, `GET /api/logs/{sid}/audit`, 화면(답변 '점검' 배지, 실행 과정 맨 아래, 실행 로그 상세의 '이상 징후 판정'). 설계와 필수 기능 대비 비교는 [AGENT.md](AGENT.md) 8장. 테스트 `tests/test_audit.py` · 체크리스트 28번 |
| F3021 Planner·Worker 분리 | 선택하지 않음 |
| F3022 RAG 검색 Tool | 선택하지 않음 |
| F3023 캘린더 연동 Tool | 선택하지 않음 |

## 비기능 요구사항

| 번호 | 내용 | 확인 |
|---|---|---|
| NF3001 | API 키는 환경변수 | `app/config.py` 에서 `.env` 만 읽음. 25번이 저장소 전체에서 키 문자열을 찾음 |
| NF3002 | 화면에 키·오류 상세 노출 금지 | `common.scrub`, 내부 오류는 "처리 중 오류"로만 안내. 25번 |
| NF3003 | Tool 실패해도 서비스 유지 | 모든 도구가 봉투로 실패 반환, 보조 도구 실패는 기록 후 계속. 7번 |
| NF3004 | 재계획 상한 | `REPLAN_LIMIT = 3`, LangGraph `recursion_limit=40`. 10번 |
| NF3005 | 응답 시간 | 같은 요청 캐시(5분), 페이지 상한, LLM 시간 제한 20초. 23번이 대표 요청별 처리 시간을 기록 (기준 20초) |
| NF3006 | 프론트/백 분리, Agent·Tool 파일 분리 | `web/` ↔ `app/`, `app/agent/` ↔ `app/tools/`. 26번 |
| NF3007 | 출처·이용 조건 기록 | README 표, 화면 하단, 답변의 '근거 자료'. 26번 |

## 대표 시나리오 (4.7)

| # | 유형 | 요청 | 기대 동작 |
|---|---|---|---|
| S1 | 한 번에 충족 | 내일 인천공항에 9시 도착하는데 탈 수 있는 오사카행 항공편 | 09:00 + 120분 이후 출발 편 중 가장 이른 편, 이른 편은 '준비 시간 부족'으로 제외, 재계획 없음 |
| S2 | 재계획 필요 | 김포에서 제주 가는 가장 빠른 편, 23시 59분 이후로 | 23:59 이후 편이 없어 시간 조건을 풀고 재계획 → 추천, 같은 입력 재호출 없음 |
| S3 | 상한 도달 | 빠른 시일 내에 김포에서 센다이 가는 편 | 재계획 3회 후 멈추고 '재계획 상한에 도달'과 조건 변경 제안 |
| S4 | Tool 하나 실패 | 김포에서 제주 가는 가장 빠른 편 (혼잡도 도구를 일부러 실패) | 실패를 기록하고 추천까지 진행 |
| S5 | Tool 불필요 | 안녕! 뭘 할 수 있어? | 도구 호출 0회로 사용법 안내 |
| S6 | 기준 다름(추정) | (30일 뒤 날짜) 김포에서 제주 가는 가장 빠른 편 | 같은 요일 운항표로 조회하고 '추정'을 밝힘 |

실행 결과·실행 로그·처리 시간·기대와의 차이는 `python verify/run_checks.py` 가 [verification/SCENARIOS.md](verification/SCENARIOS.md) 와 [samples/](samples/) 에 남깁니다.

## 아직 실제 API 로 확인하지 못한 것 (응답 필드 이름)

- 한국공항공사 실시간 info 응답의 목적지 필드 이름(`arrivedKor`, `boardingKor`)과 `numOfRows` 적용 여부 — 다르면 목적지 이름만 비고 탑승구는 정상.
- 한국공항공사 도착 응답의 예상 시각 필드(`estimateddatetime`) — 다르면 예상 시각만 빔.
- 인천 승객 예고 필드의 뜻(dg=출국, eg=입국) — 필드 이름 규칙으로 읽은 것.
- 시간대별 탑승승객 수(daily-expect-passenger)는 응답 항목 이름이 문서에 없어 형식을 추정해 읽음. 읽지 못하면 자동으로 운항 편수 기준으로 대신함. `python api_check/check_apis.py --group kac` 출력(검증 때 `docs/verification/api_check.txt`)으로 확인.
