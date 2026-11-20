# 검증 결과 (명세 4장 · 6장 5)

`python verify/run_checks.py` 를 2026-11-20 15:49 에 실행한 결과입니다 (실제 공공데이터 API + SSAFY GMS, 총 106초). 같은 FastAPI 앱을 그대로 불러 요청을 보내고, 항목마다 실행 결과를 증빙 파일로 남겼습니다.

**요약:** 통과 27 · 실패 0 · 화면 확인 필요 0 · 해당없음 5

| 문서 | 내용 |
|---|---|
| [verification/RESULTS.md](verification/RESULTS.md) | 셀프 체크리스트 1~32번 항목별 결과·비고 |
| [verification/evidence/](verification/evidence/) | 항목별 실행 결과(요청·응답·이벤트) |
| [verification/SCENARIOS.md](verification/SCENARIOS.md) | 대표 시나리오 6개: 최종 응답, 실행 단계, Tool 호출 이력, 재계획, 처리 시간, 기대와 실제의 차이 |
| [samples/](samples/) | 실행 로그 샘플: 대표 요청 전체(full_run_S1), 재계획(replan_S2), 상한 도달(replan_limit_S3), 도구 실패(tool_failure_S4) |
| [verification/PLANNING_CHECK.md](verification/PLANNING_CHECK.md) | 기획 검증: 외부 API 호출, LLM 연결·Tool 선택, 설계 시험 |
| [verification/graph.md](verification/graph.md) | LangGraph 노드 구성 (분석 → 계획 → 실행 → 판단 → 응답) |
| [verification/versions.md](verification/versions.md) | 명세 1.2 필수 라이브러리 버전 대조 |
| [셀프체크리스트.xlsx](셀프체크리스트.xlsx) | 제공 양식에 결과·증빙·비고를 적은 셀프 체크리스트 |

## 항목별 결과

| 번호 | 요구사항 | 확인 항목 | 결과 | 비고 |
|---|---|---|---|---|
| 1 | F3001 | Tool 명세·등록, 정상/실패 호출 형식 | 통과 | 도구 11개 (외부 9 · 로컬 2). 로컬 도구는 키가 필요 없거나(공항 검색) 묶은 외부 도구의 실패를 parts 에 담아 돌려준다. |
| 2 | F3002 | 기준 날짜 기본값, 요청·조회 기준 차이 명시 | 통과 | 기본값=오늘(20261120). 범위 밖 날짜는 같은 요일 날짜로 조회하고 note·답변에 '추정'을 밝힘 |
| 3 | F3002 · F3003 | 식별자로 상세 조회 연결, 빈 속성은 판단하지 않음 | 통과 | RS6347 → get_flight_detail 1건. 시각이 빈 편은 '판단하지 않음'으로 제외 |
| 4 | F3004 | 보조 정보 이름·주소·링크, 결과 없음 형식 | 통과 | 결과마다 name·location·hours·map_url. 결과 없음은 count 0 + empty true + note '결과 없음: …' |
| 5 | F3005 | 기준·선호·제약 추출, 없는 조건은 비움 | 통과 | 분석 출처 llm. 빠진 요청은 date·시각·제약을 비워 둠 |
| 6 | F3006 | 계획 구성, 도구 불필요 요청, 그래프 노드 | 통과 | 인사 → 계획 steps=[] · 도구 0회. 출발 편 → search_departures(+보조) 순서. 그래프 노드 = AGENT.md 단계 (graph.md) |
| 7 | F3007 · NF3003 | 앞 결과 → 다음 입력, Tool 실패해도 진행 | 통과 | 상세 조회 입력 = 판단에서 고른 편명. 보조 도구 실패(get_airport_congestion) 기록 후 응답까지 진행, 다음 요청 정상 |
| 8 | F3008 | 계산 값과 판단 기준 일치, 확인 불가 값은 역산·추정 표시 | 통과 | 기준 09:00+120분=11:00, 추천 11:05, 늦어도 도착 09:05(역산), 체크인 마감은 '추정' 표기 |
| 9 | F3009 | 재계획과 결과 재사용 | 통과 | 재계획 1회, 재사용 1건, 같은 입력 재호출 없음 |
| 10 | F3009 · NF3004 | 재계획 상한에서 멈춤 | 통과 | 재계획 3회 후 멈춤, '재계획 상한에 도달' 안내 (3317ms) |
| 11 | F3010 | 대화 맥락: 바뀐 조건만 갱신 | 통과 | 출발 공항만 GMP→PUS, 목적지 CJU·날짜 20261121 유지 |
| 12 | F3011 | 추천·보조 정보·판단 기준·근거·제외 사유 | 통과 | 추천·판단 기준·근거 자료·보조 정보 함께 표시, 제외 16편은 사유와 편명 표시 |
| 13 | F3011 | 위험 도메인 안내 (해당 도메인만) | 해당없음 | 위험 도메인이 아님. 대신 '운항 정보는 실시간으로 바뀝니다' 안내를 붙임 |
| 14 | F3012 · F3013 | 이벤트 발행·세션 저장·LLM 판단 이유 | 통과 | 세션 2개 모두 발행 = 저장, 섞임 없음. 의도 판단(llm) 이유: 내일 인천공항에 오전 9시 도착한 뒤 이용 가능한 오사카행 출발 항공편을 찾는 요청이다. |
| 15 | F3012 | 로깅 Agent 없이 메인 Agent 동작 | 통과 | 구독 해제 상태로 정상 응답 (1521ms), 저장된 로그 없음(HTTP 404) |
| 16 | F3013 | 로깅 Agent 수집·저장에 LLM 미사용 | 통과 | 정상 세션(판정 normal) 수집·저장 중 로깅 Agent LLM 호출 0회 |
| 17 | F3013 | 원본 그대로 저장, 저장 실패해도 대화 동작 | 통과 | 저장 로그 = 발행 이벤트 원본(요약·가공 없음). 저장 실패를 일으켜도 대화 정상 |
| 18 | F3014 | 로그 목록·상세·삭제, 수정 API 없음 | 통과 | GET 목록·GET 상세(순서대로)·DELETE 동작. /api/logs 경로에 PUT·PATCH·POST 없음 |
| 19 | F3015 | 계획 저장·조회, 겹침 알림 | 통과 | 저장·목록·상세에 핵심 값과 생성 일시(created_at). 겹치는 B 저장 시 경고: ["'검증 계획 A'(#26)과 공항 준비 시간이 겹칩니다."] |
| 20 | F3016 | 계획 수정·삭제와 화면 반영 | 통과 | API 수정·삭제 → 목록 반영 확인, 화면 캡처 전·후 2장(20_plan_before.png · 20_plan_after.png) 첨부 |
| 21 | F3017 · F3018 | API 경로·형식, 화면이 API 호출 | 통과 | 대화·계획·로그 경로 분리, /docs 스키마, 화면이 /api/chat·/api/plans·/api/logs 호출 — 캡처 21_docs_paths.png·21_docs_schemas.png·21_network.png 첨부 |
| 22 | F3019 | 실행 단계·재계획 지점·처리 중·실패·키 누락 안내 | 통과 | 실행 순서·재계획 지점·처리 중·도구 실패·키 누락 안내를 이벤트와 화면 캡처 4장으로 확인 |
| 23 | 필수 · NF3005 | 대표 시나리오 5종 이상과 처리 시간 | 통과 | 6개 유형 모두 기대대로. 처리 시간 S1 3.1s, S2 2.5s, S3 3.3s, S4 2.7s, S5 1.3s, S6 4.3s |
| 24 | 필수 | 실행 로그 샘플 (전체 1건 + 재계획) | 통과 | docs/samples/ 에 full_run_S1.json, replan_S2.json, replan_limit_S3.json, tool_failure_S4.json |
| 25 | NF3001 · NF3002 | 키 미포함, 화면에 키·오류 상세 미노출 | 통과 | 파일 101개에서 키 문자열 없음(.env 제외·.gitignore 포함). 오류 응답은 코드+안내 문구만 |
| 26 | NF3006 · NF3007 | 모듈 분리, 출처·이용 조건 기록 | 통과 | web/ ↔ app/ 분리, Agent 7개·Tool 11개 파일. README 에 출처·한도·이용 조건 |
| 27 | 필수 | 제출 전 점검 | 통과 | 필수 F3001~F3019 동작, 제출물 모두 있음, 화면 캡처 9장 |
| 28 | F3020 | 이상 징후 판단 (심화) | 통과 | 반복·재계획 과다·계획 외 호출 각각 경고, 근거 없음은 판정 불가, 정상 실행 LLM 0회, 이상 실행만 진단(LLM), 다음 질문에서 audit_referenced |
| 29 | F3021 | Planner·Worker (심화) | 해당없음 | 선택하지 않은 심화 기능 (F3020 만 선택) |
| 30 | F3021 | Worker LLM 조건 (심화) | 해당없음 | 선택하지 않은 심화 기능 (F3020 만 선택) |
| 31 | F3022 | RAG 검색 Tool (심화) | 해당없음 | 선택하지 않은 심화 기능 (F3020 만 선택) |
| 32 | F3023 | 캘린더 연동 Tool (심화) | 해당없음 | 선택하지 않은 심화 기능 (F3020 만 선택) |

## 대표 시나리오 요약 (4.7)

| # | 유형 | 요청 | 재계획 | 처리 시간 | 기대와 일치 |
|---|---|---|---|---|---|
| S1 | 한 번에 충족 | 내일 인천공항에 9시 도착하는데 탈 수 있는 오사카행 항공편 | 0 | 3.1초 | 예 |
| S2 | 재계획 필요 | 김포에서 제주 가는 가장 빠른 편, 23시 59분 이후로 | 1 | 2.5초 | 예 |
| S3 | 상한 도달 | 빠른 시일 내에 김포에서 센다이 가는 편 | 3 | 3.3초 | 예 |
| S4 | Tool 하나 실패 | 김포에서 제주 가는 가장 빠른 편 | 0 | 2.7초 | 예 |
| S5 | Tool 불필요 | 안녕! 뭘 할 수 있어? | 0 | 1.3초 | 예 |
| S6 | 기준 다름(추정) | 12월 20일 김포에서 제주 가는 가장 빠른 편 | 0 | 4.3초 | 예 |

## 화면 캡처

![20_plan_after.png](verification/screens/20_plan_after.png)
![20_plan_before.png](verification/screens/20_plan_before.png)
![21_docs_paths.png](verification/screens/21_docs_paths.png)
![21_docs_schemas.png](verification/screens/21_docs_schemas.png)
![21_network.png](verification/screens/21_network.png)
![22_nokey.png](verification/screens/22_nokey.png)
![22_pending.png](verification/screens/22_pending.png)
![22_replan.png](verification/screens/22_replan.png)
![22_toolfail.png](verification/screens/22_toolfail.png)
