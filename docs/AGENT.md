# Agent 설계 문서

## 1. 전체 구조

```
사용자 ──POST /api/chat──▶ 메인 에이전트 (LangGraph)
                              │ emit(event, session_id, payload)
                              ▼
                         EventBus ──구독──▶ 로깅 에이전트 (LLM 미사용) ──▶ data/planner.sqlite3 (events)
                              │
                              └──▶ 응답의 trace (화면 '실행 과정')
```

- 메인 에이전트는 이벤트를 **발행만** 합니다. 누가 구독하는지 모르고, 구독자가 없거나 고장 나도 대화는 그대로 동작합니다 (`app/events.py`).
- 로깅 에이전트는 이벤트를 받는 즉시 SQLite `events` 표에 발생 순서대로 저장합니다. 요약·가공하지 않고, 수정 API 가 없습니다 (`app/logger_agent.py`).
- 화면에서 직접 부른 조회(시간표·시설 등)도 `screen` 세션으로 같은 로그에 남습니다.

## 2. 실행 단계와 상태 (F3005 ~ F3011)

```
analyze → plan → execute → judge ─┬─ replan  → plan     (후보 0건, 넓힐 조건 있음, 3회 미만)
                                  ├─ enrich  → respond  (고른 편의 상세 보강)
                                  └─ respond → END
```

| 단계 | 입력 (state) | 출력 | 내용 |
|---|---|---|---|
| analyze | message, memory | intent, intent_source, inherited | LLM(GMS) 구조화 출력 또는 규칙 기반으로 의도와 조건을 뽑는다. 말하지 않은 값은 null. 직전 대화 조건과 병합 |
| plan | intent, replans | steps, missing, assumptions, ctx | 의도별 도구와 순서. 기본값을 쓰면 '가정'으로 기록. 조건이 모자라면 missing(되물음) |
| execute | steps, cache | results, cache | 도구 호출. 같은 인자 호출은 재사용(재계획 때 재호출하지 않음). 실패해도 다음 단계 진행 |
| judge | results, intent, ctx | decision, judgement, replans | 제약 판단, 재계획 여부 |
| enrich | judgement.chosen | judgement, results | 고른 편명을 상세 도구 입력으로 넘김 (한국공항공사 공항은 탑승구가 여기서 채워짐) |
| respond | 위 전부 | answer, cards, summary | 추천·판단 기준·제외 사유·가정·근거 자료·안내 |

### 의도 (intent)

`departures`(출발 편·가장 빠른 편) · `arrivals` · `flight_detail` · `congestion` · `passenger` · `facilities` · `plan` · `greeting` · `unknown`

### 조건 (F3005)

| 구분 | 필드 |
|---|---|
| 기준 | date, from_time, to_time, airport(기준 공항) |
| 선호 | destination / origin(도시·공항), earliest, keyword(시설) |
| 제약 | arrive_airport_at(공항 도착 시각), buffer_min(준비 시간) |

LLM 이 뽑은 값은 `normalize` 가 형식을 다시 검사하고, 틀린 값(존재하지 않는 날짜, 9999 같은 시각, 잘못된 편명)은 버립니다.

## 3. 계획 수립 규칙 (F3006)

| 의도 | 계획 | 기본값(가정으로 표시) |
|---|---|---|
| departures | search_departures → (보조) 혼잡도 또는 인천 승객 예고 | 출발 공항 없음: 해외 목적지는 인천, 국내 목적지는 김포 / 날짜 없음: 오늘 |
| arrivals | search_arrivals → (보조) | 도착 공항 없음: 인천 |
| flight_detail | search_flight | 공항 없음: 인천 → 김포 → 김해 → 제주 순으로 찾음 |
| congestion | 인천: 승객 예고 / 김포·제주·김해·청주·대구: 실시간 혼잡도 + 시간대별 흐름 / 그 외 한국공항공사 공항: 시간대별 흐름 | 혼잡도 API 가 없는 공항은 '흐름으로 대신'을 가정으로 표시 |
| facilities | 인천: get_incheon_facilities / 그 외: 상업시설 계약 | 공항 없음: 인천 |
| plan, greeting, unknown | 도구 없음 (계획 단계를 건너뜀) | — |

## 4. 제약 판단과 재계획 (F3008 · F3009)

**판단 계산식 (출발 편)**

- 공항 도착 시각을 말했으면: 기준 = 공항 도착 + 준비 시간(말하지 않으면 국제선 120분·국내선 60분 기본값). 출발(예상 출발이 있으면 그 시각) ≥ 기준인 편만 후보.
- 오늘이고 도착 시각을 말하지 않았으면: 기준 = 지금. 이미 지난 편 제외.
- 결항 편, 이미 '출발'로 표시된 편 제외.
- 목적지가 도시(예: 도쿄)면 그 도시의 모든 공항(NRT·HND)을 목적지로 본다.
- 고른 편마다 **늦어도 공항 도착할 시각 = 출발 − 준비 시간**을 역산해 보여 준다. 체크인 마감은 항공사마다 달라 API 로 확인할 수 없으므로 지어내지 않는다.

**재계획 조건과 순서** (후보가 0건일 때만)

1. 시간 조건(from/to, 공항 도착 시각)을 말했으면 → 시간 조건을 풀고 같은 날 전체
2. 날짜를 말하지 않았거나 '빠른 시일 내'면 → 1일 뒤, 2일 뒤, 3일 뒤
3. 편명 조회에서 공항을 말하지 않았으면 → 다음 공항

- 상한: `REPLAN_LIMIT = 3` (NF3004). 상한에 닿으면 멈추고 "재계획 상한에 도달"과 조건 변경 제안을 답한다.
- 재계획하지 않는 경우: `NO_KEY`, `BAD_ARGS` 처럼 다시 해도 결과가 같은 실패 → 바로 안내. 네트워크·제공기관 오류는 같은 조건으로 1회만 재시도.
- 재계획 때 이미 받은 도구 결과는 캐시에서 재사용하고 `tool_reused` 이벤트를 남긴다.

## 5. 대화 맥락 (F3010)

- 보관(SQLite `chat_memory` 표, 서버를 다시 켜도 이어짐): 세션별 직전 의도·조건, 기본값으로 정한 공항, 추천한 편명, 최근 6개 대화 요약. 도구 원본 응답은 보관하지 않는다 (`app/agent/memory.py`).
- 갱신: 새 요청에서 말한 값만 바꾸고 나머지는 이어받는다. 예) "센다이 가는 가장 빠른 편" → "김포에서는?" = 목적지 센다이 유지, 출발 공항만 김포.
- LLM 에도 직전 조건을 프롬프트로 넘겨 '내일은?' 같은 문장을 같은 의도로 해석하게 한다. 이어받은 키는 `intent_analyzed` 이벤트의 `inherited` 에 남는다.
- '새 대화' 버튼(`DELETE /api/chat/{sid}`)으로 비운다.

## 6. 이벤트 (F3012)

| 이벤트 | 주요 내용 |
|---|---|
| request_received | 질문(앞 200자), 맥락 유무 |
| step_started / step_finished | 단계 이름, 소요 ms |
| intent_analyzed | source(llm/rule), intent, reason(LLM 판단 이유), conditions, inherited, note |
| planned | attempt, steps(도구·인자), followups(후보를 고른 뒤 부를 수 있는 후속 도구: 출발·도착 조회면 `get_flight_detail`), missing, assumptions |
| tool_called / tool_result / tool_reused | 도구, 인자, ok, 실패 코드, 소요 ms, 건수 |
| replanned | attempt, reason |
| judged | decision, 후보·제외 수, 판단 기준, 고른 편 |
| detail_skipped | 상세 조회를 생략한 이유 |
| responded, memory_updated | 응답 길이·카드 수, 맥락 요약 |
| engine_fallback | langgraph 가 없을 때 같은 순서로 직접 실행했다는 사실 |
| screen_query | 화면 직접 조회 (세션 `screen`) |
| cancelled | '새 대화'로 멈춘 실행 (대화 맥락에는 남기지 않음. 이상 징후 판정은 응답이 없으므로 '판정 불가') |
| audit_referenced | 메인 에이전트가 읽은 직전 실행의 이상 징후 판정 (run_index, status, 경고 코드) — F3020 |

## 7. LLM 을 쓰는 곳과 쓰지 않는 곳

| 위치 | LLM | 이유 |
|---|---|---|
| 요청 분석(analyze) | 씀 (GMS 키 있을 때) | 자연어 → 구조화. 실패하면 규칙 기반으로 넘어가고 그 사실을 기록 |
| 계획·판단·재계획·응답 | 쓰지 않음 | 규칙으로 판단 가능. 계산 값을 지어내지 않게 하려고 |
| 로깅 에이전트 (수집·저장·이상 징후 판정) | 쓰지 않음 | 명세 F3013 · F3020. 판정은 이벤트 대조와 횟수 세기 |
| 로깅 에이전트 (원인 진단) | 씀 (이상 판정 실행만, GMS 키 있을 때) | 정상·판정 불가 실행에는 부르지 않음. 진단은 판정과 따로 저장 |

## 8. 이상 징후 판단 (심화 F3020)

로깅 에이전트가 실행(질문 1건)이 끝날 때마다(`memory_updated`, `agent_error`) 그 실행의 이벤트로 판정합니다.
코드: `app/audit.py`(규칙·진단), `app/logger_agent.py`(트리거·저장), `app/store/audits.py`(audits 표).

| 경고 코드 | 규칙 (LLM 없음) |
|---|---|
| `REPEATED_CALL` | 같은 도구를 같은 인자로 다시 부름. 이미 성공한 결과가 있는데 또 부르거나, 실패 뒤 재시도 1회(판단 노드 설계)를 넘겨 부르면 경고 |
| `EXCESSIVE_REPLAN` | 재계획 횟수가 상한(`REPLAN_LIMIT`=3)에 이름 |
| `UNPLANNED_CALL` | 직전 `planned`의 steps(도구+인자)에도 followups(도구)에도 없는 호출 |

- 상태: 경고가 하나라도 있으면 `anomaly`(이상), 근거 이벤트가 모자라면 `undetermined`(판정 불가), 그 밖은 `normal`(이상 없음).
- 판정 불가가 되는 경우: 시작(`request_received`)·끝(`responded`) 이벤트가 없음, 도구를 불렀는데 앞에 `planned`가 없음. 근거가 없을 때 '이상 없음'으로 처리하지 않습니다.
- 원인 진단: `anomaly`인 실행에만 LLM이 실행 기록과 경고를 읽고 세 문장 이내로 "어느 쪽 문제인지 / 근거 이벤트 / 고칠 점"을 씁니다. 응답이 늦어지지 않게 백그라운드에서 돌고, 결과는 `diagnosis` 칸에 판정과 따로 저장되며 경고 수에 섞이지 않습니다. 키가 없으면 `skipped`, 실패하면 `failed`로 남고 판정과 실행 로그는 그대로입니다.
- 메인 에이전트 참조: 새 질문을 시작할 때 같은 세션의 직전 판정을 `audit_store.latest(session_id)`로 읽어 상태(`prev_audit`)에 싣고 `audit_referenced` 이벤트로 남깁니다. 대화 응답(`POST /chat`)에도 이번 실행의 판정(`audit`)이 함께 옵니다.
- 실행 로그는 그대로 두고 판정은 다른 표(`audits`)에 둡니다. 로그를 지우면 그 세션의 판정도 지웁니다.

### 필수 기능과의 비교

| 항목 | 필수(F3013·F3014)만 있을 때 | F3020 적용 후 |
|---|---|---|
| 같은 도구 반복 | 로그를 사람이 읽어야 알 수 있음 | `REPEATED_CALL` 경고와 근거 이벤트 번호 |
| 재계획 상한 도달 | 재계획 칩 개수로만 보임 | `EXCESSIVE_REPLAN` 경고 |
| 계획 밖 호출 | 확인 수단 없음 | `planned`와 대조해 `UNPLANNED_CALL` |
| LLM 호출 수 | 0 | 정상 실행 0, 이상 실행 1 (진단) |
| 응답 시간 | — | 판정은 규칙이라 수 ms, 진단은 백그라운드라 응답에 영향 없음 |
