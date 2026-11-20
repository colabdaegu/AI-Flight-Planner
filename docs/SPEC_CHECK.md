# 명세 대조표 · 검증 기록

명세서(AI 코어 프로젝트 3회차) 요구사항별로 어디에 구현했고 어떻게 확인하는지 정리했습니다.
"테스트"는 `python -m pytest -q` (가짜 응답, 네트워크·키 불필요)에 들어 있는 항목입니다.

## 기능 요구사항 (필수)

| 번호 | 요구사항 | 구현 위치 | 확인 방법 |
|---|---|---|---|
| F3001 | Tool 명세 정의·등록, 외부/로컬 구분 | `app/tools/registry.py` (Tool.kind), [TOOLS.md](TOOLS.md) | `GET /api/tools`, 테스트 `test_tools_are_marked_external_or_local` |
| F3002 | 후보 조회 Tool, 기본값, 추정 사실 명시 | `search_departures/arrivals/flight` (`app/tools/flights.py`) | 날짜 생략=오늘, 오늘은 지금 시각부터. 일부만 받으면 note. 테스트 `test_today_without_time_starts_from_now`, `test_pages_are_fetched_until_total_reached` |
| F3003 | 상세정보 조회, 빈 값 임의로 채우지 않음 | `get_flight_detail` + 노드 `enrich` | 테스트 `test_enrich_passes_chosen_flight_to_detail_tool`, `test_kac_detail_today_uses_realtime_info_with_gate` |
| F3004 | 보조 정보 검색 (다른 출처) | 혼잡도·승객 예고·시설 안내·상업시설 | 출발 편 계획에 보조 단계 포함: `test_plan_defaults_origin_and_says_so` |
| F3005 | 요청 분석 (기준·선호·제약) | `app/agent/llm.py` | 테스트 `test_sendai_request_…`, `test_origin_destination_date_and_time`, `test_airport_arrival_time_constraint`, `test_normalize_drops_bad_values…` |
| F3006 | 작업 계획 수립, 불필요 Tool 제외 | 노드 `plan` | `test_plan_without_tools_for_greeting`, `test_greeting_calls_no_tools` |
| F3007 | Tool 호출·결과 통합, 앞 결과 → 다음 입력, 실패해도 진행 | 노드 `execute`, `enrich` | `test_enrich_…`, `test_execute_reuses_results_on_replan` |
| F3008 | 제약 적합성 판단, 확인 불가 값은 역산 기준 제시 | `evaluate_flights` (공항 도착+준비 시간, 결항, 지난 편), 응답의 '늦어도 HH:MM까지' | `test_evaluate_applies_airport_arrival_and_buffer` |
| F3009 | 재계획 (결과 재사용, 상한) | 노드 `judge`, `_widen_options`, 캐시 | `test_judge_replans_until_cap_then_reports`, `test_full_flow_replans_to_next_day_and_remembers` |
| F3010 | 대화 맥락 유지 (바뀐 조건만 갱신) | `app/agent/memory.py`(SQLite), `merge_context`, LLM 프롬프트에 직전 조건 | `test_followup_keeps_conditions…`, `test_followup_changes_origin_only`, 전체 흐름 테스트 2번째 질문 |
| F3011 | 최종 응답: 추천·근거·제외 사유·판단 기준 | 노드 `respond` | 전체 흐름 테스트가 '추천', '판단 기준', '근거 자료', '가정' 포함 확인. 위험 도메인 아님(여행) → 대신 "운항 정보는 바뀝니다" 안내 |
| F3012 | 실행 이벤트 발행 (단계·Tool·LLM 판단) | `app/events.py`, `graph._step`, 각 노드 emit | 이벤트 목록은 [AGENT.md](AGENT.md) 6장. 발행은 구독자와 무관 |
| F3013 | 로깅 Agent 구독·세션 저장, LLM 미사용 | `app/logger_agent.py` (SQLite events 표, 서버 재시작 후에도 유지) | `test_plans_logger.py` |
| F3014 | 로그 목록·상세·삭제, 수정 불가 | `GET/DELETE /api/logs`, 화면 '실행 로그' | 수정 API 없음 ([API.md](API.md)), `test_log_list_shows_first_question_and_counts` |
| F3015 | 계획 저장·조회, 날짜 포함, 겹침 알림 | `app/store/plans.py` (편명으로 출발 시각 자동 조회, 공항 체크인 시간 입력값(없으면 국내선 60분·국제선 120분)으로 체크인 마감), AI 추천 → '이 편으로 계획 저장' | 겹치면 저장 + warnings. `test_plans_logger.py` |
| F3016 | 계획 수정·삭제, 화면 반영 | `PUT/DELETE /api/plans/{id}`, 화면 수정 버튼 | 수정 후 목록 다시 불러옴 |
| F3017 | FastAPI 백엔드 (대화·계획·로그 경로 분리, Pydantic) | `app/api/routes.py`, `app/main.py` | `/docs` |
| F3018 | HTML/CSS/JS 화면, 백엔드 API 사용 | `web/` | 대화 화면 = 첫 화면, 계획 화면 |
| F3019 | 실행 단계·Tool 이력·재계획 지점 표시, 처리 중·키 누락 안내 | 화면 오른쪽 '실행 과정', 처리 중 표시, 키 누락 배너 | 재계획은 주황색으로 구분 |

## 심화 (선택)

| 번호 | 상태 |
|---|---|
| F3020 로깅 Agent 이상 징후 판단 | **구현.** `app/audit.py`, `app/logger_agent.py`, `app/store/audits.py`, `GET /api/logs/{sid}/audit`, 화면(답변 '점검' 배지, 실행 과정 맨 아래, 실행 로그 상세의 '이상 징후 판정'). 설계와 필수 기능 대비 비교는 [AGENT.md](AGENT.md) 8장. 테스트 `tests/test_audit.py`: 반복 호출·재계획 과다·계획 밖 호출 각각 식별, 근거 없으면 판정 불가, 정상 실행엔 LLM 미호출, 이상 실행만 진단(판정과 분리, 실패해도 판정 유지), 메인 에이전트가 직전 판정 참조(`audit_referenced`), 로그 삭제 시 판정도 삭제 |
| F3021 Planner·Worker 분리 | 미구현 |
| F3022 RAG 검색 Tool | 미구현 (2회차 Vector DB 필요) |
| F3023 캘린더 연동 Tool | 미구현 |

## 비기능 요구사항

| 번호 | 내용 | 확인 |
|---|---|---|
| NF3001 | API 키는 환경변수 | `app/config.py` 에서 `.env` 만 읽음. 저장소에 키 없음 |
| NF3002 | 화면에 키·오류 상세 노출 금지 | `common.scrub` 로 키 가림, 내부 오류는 "처리 중 오류" 로만 안내 |
| NF3003 | Tool 실패해도 서비스 유지 | 모든 도구가 봉투로 실패 반환, 공항 개요는 부분 실패 허용 |
| NF3004 | 재계획 상한 | `REPLAN_LIMIT = 3`, LangGraph `recursion_limit=40` |
| NF3005 | 응답 시간 | 같은 요청 캐시(5분), 페이지 나눠 받기 최대 4쪽, LLM 시간 제한 20초 |
| NF3006 | 프론트/백 분리, Agent·Tool 파일 분리 | `web/` ↔ `app/`, `app/agent/` ↔ `app/tools/` |
| NF3007 | 출처·이용 조건 기록 | README 표, 화면 하단, 답변의 '근거 자료' |

## 대표 시나리오 (4.7)

| # | 요청 | 기대 동작 | 오프라인 검증 |
|---|---|---|---|
| 1 | 인천공항에 9시 도착하는데 탈 수 있는 오사카행 | 한 번에 충족: 09:00+120분 이후 출발 편 중 가장 이른 편, 이른 편은 '준비 시간 부족'으로 제외 | `test_evaluate_applies_airport_arrival_and_buffer` |
| 2 | 빠른 시일내에 센다이에 가고 싶어 | 출발 공항 인천 가정, 오늘 편 없음 → 재계획(다음 날) → 추천 | `test_full_flow_replans_to_next_day_and_remembers` |
| 3 | (이어서) 김포에서는? | 목적지 유지, 출발 공항만 변경. 편이 없으면 3회 재계획 후 상한 도달 안내 | 전체 흐름 테스트 2번째 질문, `test_judge_replans_until_cap_then_reports` |
| 4 | 키 없이 '김포 출발 시간표' | NO_KEY → 재계획 없이 설정 안내, 화면 배너 | `test_judge_does_not_retry_missing_key…` |
| 5 | 안녕 / 사용법 | 도구 호출 없이 안내 | `test_greeting_calls_no_tools` |
| 6 | 인천 지금 붐벼? | 인천은 혼잡도 API 대신 승객 예고로 대체(가정 표시) | 화면 확인 |

실제 공공데이터로 돌린 실행 로그는 서버를 켠 뒤 위 요청을 보내고 '실행 로그'에서 원본을 복사해 제출물에 붙입니다(`data/logs/<세션>.jsonl`).

## 아직 실제 API 로 확인하지 못한 것

- 한국공항공사 실시간 info 응답의 목적지 필드 이름(`arrivedKor`, `boardingKor`)과 `numOfRows` 적용 여부 — 다르면 목적지 이름만 비고 탑승구는 정상.
- 한국공항공사 도착 응답의 예상 시각 필드(`estimateddatetime`) — 다르면 예상 시각만 빔.
- 인천 승객 예고 필드의 뜻(dg=출국, eg=입국) — 필드 이름 규칙으로 읽은 것.
- 인천 여객기 서비스의 `airport_code` 필터 — 무시되더라도 앱이 한 번 더 거르므로 결과는 맞음.
- 한국공항공사 상업시설 임대계약(airport-retail-contract)은 코드 04로 실패해 전국공항 임대계약 정보(airport-lease-contract)로 교체함.
- 시간대별 탑승승객 수(daily-expect-passenger)는 기본 주소에서 코드 12(서비스 없음). /info, /list 를 차례로 시험하고, 모두 실패하면 30분 동안 다시 부르지 않고 운항 편수로 대신함.
- 시간대별 탑승승객 수(daily-expect-passenger)는 응답 항목 이름이 문서에 없어 형식을 추정해 읽음. `python api_check/check_apis.py --group kac` 가 항목 이름을 출력하니 확인 후 맞추면 됨. 읽지 못하면 자동으로 운항 편수 기준으로 대신함.
