# 항공 여행 일정 플래너 (AI 코어 프로젝트 3회차 · Agent 기반 도메인 플래너)

만든 사람: 김현수 (SSAFY 16기 모바일 트랙)

"센다이 가는 가장 빠른 편", "인천공항에 9시 도착인데 탈 수 있는 오사카행"처럼 말로 물으면
에이전트가 요청을 분석하고, 공공데이터(인천국제공항공사·한국공항공사) 도구를 골라 부르고,
공항 도착 시각·준비 시간 같은 제약과 비교해 항공편을 추천합니다. 맞는 편이 없으면 조건을 넓혀 다시 계획합니다(최대 3회).
추천받은 편은 '내 계획'에 저장해 체크인 마감과 겹치는 일정을 관리할 수 있습니다.

## 화면

| 메뉴 | 내용 |
|---|---|
| AI 플래너 (첫 화면) | 대화, 추천 항공편 카드, '이 편으로 계획 저장', 오른쪽에 실행 과정(단계·도구 호출·재계획) |
| 출발 / 도착 시간표 | 공항 자동 완성, 달력, 보딩패스 카드, 상대 공항 미니 지도 |
| 편명 조회 | 기준 공항의 하루 출발·도착에서 편명 찾기 |
| 공항 시설 | 공항 선택(인천 기본), 지금 혼잡·시간대별 3D 막대, 인천 시설(면세점·식당·약국 …) 3D 분포와 목록 |
| 내 계획 | 저장·조회·수정·삭제, 체크인 마감·여유 시간, 같은 날 겹침 경고 |
| 실행 로그 | 세션 목록(첫 질문·도구 호출·재계획 수), 단계별 상세, 원본, 삭제 (수정 불가) |

계획·실행 로그·대화 맥락은 모두 `data/planner.sqlite3` 에 저장되어 서버를 다시 켜도 남습니다. 예전 `data/logs/*.jsonl` 로그는 처음 켤 때 자동으로 옮겨집니다. 추천 질문용 사용 기록은 브라우저(localStorage)에만 남고 서버로 보내지 않습니다.

## 실행 방법 (Windows Git Bash 기준)

```bash
cd /c/SSAFY/pennant_race_ai_core3_8
python -m venv .venv                 # 처음 한 번
source .venv/Scripts/activate
python -m pip install -r requirements.txt
cp .env.example .env                 # 처음 한 번, 아래 키를 채운다
python -m uvicorn app.main:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000/` 을 엽니다. API 문서는 `http://127.0.0.1:8000/docs`.

테스트(네트워크·키 없이 동작): `python -m pytest -q`
API 연결 확인(실제 호출): `python api_check/check_apis.py --group all`

## API 키 설정 (NF3001)

키는 `.env` 에만 넣고 코드나 문서에 적지 않습니다. `.env` 는 `.gitignore` 에 들어 있습니다.

| 변수 | 용도 | 없을 때 |
|---|---|---|
| `DATA_GO_KR_KEY` | 공공데이터포털 일반 인증키(Decoding) | 조회 도구가 `NO_KEY` 를 돌려주고 화면에 설정 안내 |
| `GMS_API_KEY` | SSAFY GMS(OpenAI 호환) — 요청 분석 LLM | 규칙 기반 분석으로 동작하고 그 사실을 답과 실행 과정에 표시 |
| `GOOGLE_MAPS_API_KEY` | (선택) 지금은 쓰지 않음. 지도는 키가 필요 없는 임베드 주소 사용 | — |

공공데이터포털에서 아래 서비스를 활용신청한 뒤 마이페이지의 일반 인증키를 넣습니다.

## 사용한 외부 API와 이용 조건 (NF3007)

| 제공 | 서비스 | 쓰는 곳 | 개발계정 하루 한도 |
|---|---|---|---|
| 인천국제공항공사 | 여객기 운항 현황 상세 조회 (StatusOfPassengerFlightsDeOdp) | 인천 출발·도착·편명 | 500 |
| 인천국제공항공사 | 승객 예고 (passgrAnncmt) | 인천 혼잡(예상 승객) | 1,000 |
| 인천국제공항공사 | 상업시설 정보 (StatusOfFacility) | 인천 시설 안내 | 1,000 |
| 인천국제공항공사 | 운항 현황 (statusOfAllFltDeOdp) | API 확인 프로그램 | 500 |
| 한국공항공사 | 실시간 운항 정보 (flight-status: depart, arrival, info) | 김포 등 14개 공항 출발·도착·탑승구 | 5,000 |
| 한국공항공사 | 공항 혼잡도 (airport-congestion v1·v2) | 김포·제주·김해·청주·대구 국내선 **지금** 구간별 혼잡(한 시점) | 5,000 |
| 한국공항공사 | 전국공항 시간대별 탑승승객 수 (daily-expect-passenger) — **호출 주소 확인 필요(코드 12)** | 14개 공항 시간대별 흐름. 안 되면 운항 편수로 대신 계산 | 5,000 |
| 한국공항공사 | 전국공항 임대계약 정보 (airport-lease-contract) | 한국공항공사 공항 매장(업체·업종·위치, 공항별) | 5,000 |
| (사용 중단) | 공항 상업시설 임대계약 (airport-retail-contract) | 승인 후에도 코드 04, 공항 구분 없음 → 위 서비스로 교체 | — |

- 출처: 공공데이터포털(data.go.kr). 각 데이터의 이용 허락 조건과 포털 이용약관을 따릅니다. 화면 하단에도 같은 내용을 표시합니다.
- 한도 보호: 같은 요청은 5분(시설 안내는 6시간) 동안 재사용합니다.
- 지도: Google 지도 임베드(`maps?q=…&output=embed`, 키 불필요). LLM: SSAFY GMS.
- 운항·혼잡 정보는 실시간으로 바뀌므로 답변마다 "출발 전 항공사 안내를 다시 확인하세요"를 붙입니다.

## 기능 구분

**필수 (F3001 ~ F3019)** — 모두 구현. 항목별 위치와 확인 방법은 [docs/SPEC_CHECK.md](docs/SPEC_CHECK.md).

- Tool 11개(외부 API 9, 로컬 2), 공통 봉투·실패 코드 — [docs/TOOLS.md](docs/TOOLS.md)
- 메인 에이전트(LangGraph): 분석 → 계획 → 실행 → 판단 → (재계획 | 상세 보강) → 응답, 대화 맥락 — [docs/AGENT.md](docs/AGENT.md)
- 로깅 에이전트: 이벤트 구독, 세션별 SQLite 저장(LLM 미사용), 목록·상세·삭제
- FastAPI 백엔드 / HTML·CSS·JS 프론트엔드 — [docs/API.md](docs/API.md)

**심화 (F3020 ~ F3023)** — **F3020 로깅 에이전트 이상 징후 판단**을 구현했습니다.

- 실행(질문 1건)이 끝날 때마다 로깅 에이전트가 규칙으로 판정: 같은 도구 같은 입력 반복(`REPEATED_CALL`), 재계획 과다(`EXCESSIVE_REPLAN`), 계획에 없던 호출(`UNPLANNED_CALL`). 근거 이벤트가 없으면 '판정 불가'.
- 이상으로 판정한 실행에만 LLM(GMS)이 원인을 세 문장 이내로 진단하고, 진단은 판정과 따로 저장합니다.
- 메인 에이전트는 다음 질문을 시작할 때 직전 판정을 읽습니다(`audit_referenced` 이벤트). 화면: 답변의 '점검' 배지, 실행 로그 상세의 '이상 징후 판정'.
- 설계와 필수 기능 대비 비교: [docs/AGENT.md](docs/AGENT.md) 8장. F3021~F3023은 선택하지 않았습니다.

## 폴더

```
app/
  agent/   graph.py(LangGraph) nodes.py(노드) llm.py(요청 분석) memory.py(대화 맥락) constraints.py(마감 계산)
  tools/   registry.py common.py flights.py congestion.py passenger.py facilities.py commercial.py airport_info.py places.py
  api/     routes.py
  store/   plans.py (계획) audits.py (이상 징후 판정)  — SQLite
  events.py  logger_agent.py  audit.py(F3020 판정·진단)  airports.py  checkin.py  db.py  config.py  main.py
web/       index.html  app.js  style.css
api_check/ check_apis.py (공공데이터 연결 확인)
tests/     pytest (가짜 응답, 네트워크 불필요)
docs/      PLAN.md(기획) AGENT.md TOOLS.md API.md SPEC_CHECK.md
data/      planner.sqlite3 (계획·실행 로그·판정·대화 맥락, 실행 중 생성, git 제외)
```
