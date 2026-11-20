"""docs/TOOLS.md(Tool 정의서)를 등록된 도구 목록에서 다시 만든다.

실행: 프로젝트 루트에서  python docs/make_tools_doc.py
- 이름 · 설명(에이전트가 보는 문장) · 입력 스키마는 app/tools/registry.py 에 실제로 등록된 값을 그대로 옮긴다.
  그래서 정의서와 코드가 어긋나지 않는다 (셀프 체크리스트 1번).
- 역할 · 출력 · 실패 경우는 아래 META 에 적는다. 도구를 추가하면 META 에 한 줄 더 적고 다시 실행한다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import tools  # noqa: E402,F401
from app.tools import registry  # noqa: E402

FLIGHT_OUT = ("`airport, airport_name, date(실제 조회 날짜), requested_date(요청 날짜), date_estimated, provider, count, total, "
              "note, flights[]` · `flights[]` 한 줄: `provider, airport, direction, airline, flight_no, scheduled, estimated, "
              "remark, terminal, gate, codeshares[], map_url, destination_code/destination_name`(출발) 또는 "
              "`origin_code/origin_name`(도착). 시각은 `YYYY-MM-DD HH:MM`.")
META = {
    "search_departures": ("후보 조회 (F3002)", FLIGHT_OUT,
                          "공항 코드가 없거나 틀림 · 날짜·시각 형식이 틀림 → `BAD_ARGS` / 키 없음 → `NO_KEY` / 결과 0건은 실패가 아니라 "
                          "`count: 0` + note. 자료 범위(오늘 −3일 ~ +6일) 밖 날짜는 같은 요일 날짜로 조회하고 `date_estimated: true` + note '추정'"),
    "search_arrivals": ("후보 조회 (F3002)", FLIGHT_OUT, "search_departures 와 같음"),
    "search_flight": ("후보 조회 · 편명 (F3002)",
                      FLIGHT_OUT + " 같은 비행의 출발·도착 줄은 합치고 `legs{departure, arrival}` 에 양쪽 시각·탑승구를 담는다. `searched`: 찾아본 공항 수.",
                      "편명 없음 → `BAD_ARGS` / 키 없음 → `NO_KEY`(모든 공항이 같은 이유로 실패하면 0건이 아니라 실패로 알림) / 못 찾으면 `count: 0` + note"),
    "get_flight_detail": ("상세정보 조회 (F3003)",
                          "`airport, flight_no, date, requested_date, date_estimated, direction, count, rows[], source, note` · rows 는 운항 한 줄과 같은 모양 "
                          "(탑승구·터미널·상태·예상 시각). 값이 없으면 `null` 로 두고 지어내지 않는다.",
                          "direction 이 departure/arrival 이 아님 · 편명 없음 → `BAD_ARGS` / 키 없음 → `NO_KEY` / 못 찾으면 `count: 0` + note"),
    "get_commercial_facilities": ("보조 정보 · 매장 (F3004)",
                                  "`airport, airport_name, count, total, hidden_business, rows[{name, business_type, location, category, zone, floor, "
                                  "map_url, contract_start, contract_end}], summary, empty, note`",
                                  "인천이나 없는 공항 코드 → `BAD_ARGS` / 키 없음 → `NO_KEY` / 결과 없음 → `count: 0, empty: true, note: '결과 없음: …'`"),
    "get_airport_congestion": ("보조 정보 · 혼잡 (F3004)",
                               "`airport, airport_name, count, rows[{time, level, level_a, level_b, level_c, extra}], segments, note` — 지금 한 시점의 국내선 구간별 단계",
                               "혼잡도 자료가 없는 공항(김포·제주·김해·청주·대구 외) → `BAD_ARGS` / 키 없음 → `NO_KEY`"),
    "get_incheon_facilities": ("보조 정보 · 시설 (F3004)",
                               "`airport, count, total, rows[{name, items, location, hours, tel, area, area_name, category, terminal, floor, map_url}], "
                               "summary{by_category, by_area, by_place}, empty, note`",
                               "area 가 D/A 가 아님 → `BAD_ARGS` / 키 없음 → `NO_KEY` / 결과 없음 → `count: 0, empty: true, note: '결과 없음: …'`"),
    "get_incheon_passenger_forecast": ("보조 정보 · 인천 예상 승객 (F3004)",
                                       "`selectdate, count, rows[{hour, t1_departure, t1_arrival, t2_departure, t2_arrival, ratio}], peak, current_hour, note`",
                                       "selectdate 가 0/1 이 아님 → `BAD_ARGS` / 키 없음 → `NO_KEY`"),
    "get_hourly_traffic": ("보조 정보 · 시간대별 흐름 (F3004)",
                           "`airport, airport_name, date, source(expected_passengers|flights), unit(명|편), rows[{hour, departure, arrival, value}], count, note, fallback_reason`",
                           "인천·없는 공항 → `BAD_ARGS` / 키 없음 → `NO_KEY` / 탑승승객 수 API 가 안 되면 운항 편수로 대신하고 fallback_reason 에 밝힘"),
    "get_airport_overview": ("화면용 묶음 (로컬)",
                             "`airport, airport_name, has_congestion, has_passenger, parts{passenger|facilities|congestion|hourly|commercial}` — 각 부분은 위 도구의 봉투 그대로",
                             "없는 공항 → `BAD_ARGS`. 묶은 외부 도구가 실패하면 그 부분만 `ok: false` 로 담는다(부분 실패 허용, NF3003)"),
    "search_airports": ("입력 보조 (로컬)", "`query, count, rows[{code, name, en, ...}]` — 외부 API 를 부르지 않는다",
                        "scope 가 all/origin 이 아님 → `BAD_ARGS`"),
}

HEAD = """# Tool 정의서

이 문서는 `python docs/make_tools_doc.py` 로 만들었습니다. **이름 · 설명 · 입력 스키마는 `app/tools/registry.py` 에 실제로 등록된 값**(`GET /api/tools`, 에이전트에 넘기는 목록)을 그대로 옮긴 것입니다.

## 공통 규칙 (F3001)

- 모든 도구는 `Tool(name, description, parameters, func, kind)` 로 레지스트리에 등록합니다. 에이전트에는 이름·설명·입력 스키마(JSON Schema)만 넘깁니다.
- 설명에는 기능이 아니라 **언제 쓰는지**를 적습니다 (명세 2.3).
- `kind`: `external` = 외부 API(공공데이터포털)를 감싼 **외부 API 툴**, `local` = 이 서버의 기능(공항 목록 검색, 여러 도구 묶음)을 감싼 **로컬 서버 API 툴**. 필수 구현 대상은 외부 API 툴입니다.
- 결과는 항상 같은 봉투입니다. 성공 `{"ok": true, "data": {...}}`, 실패 `{"ok": false, "error": {"code", "message"}}`. 예외를 밖으로 던지지 않습니다 (NF3003).
- 같은 요청은 `CACHE_TTL_SEC`(300초, 시설 안내·매장은 6시간) 동안 재사용해 하루 호출 한도를 보호합니다.
- 앞 도구의 출력 → 다음 도구의 입력: 후보 조회(`search_departures`)의 `flights[].flight_no` 중 판단 단계가 고른 편명이 상세 조회(`get_flight_detail`)의 `flight_no` 가 됩니다. 기준 공항·날짜·방향도 그대로 넘깁니다.
- 모델의 판단이 필요 없는 작업(지도 링크 만들기, 계획 저장, 체크인 마감 계산)은 Tool 로 두지 않고 실행 흐름·서버가 직접 합니다.

### 실패 코드

| 코드 | 뜻 | 에이전트 처리 |
|---|---|---|
| NO_KEY | `.env` 에 DATA_GO_KR_KEY 없음 | 재계획하지 않고 설정 안내 (화면 배너) |
| BAD_ARGS | 잘못된 입력(공항 코드, 실제로 없는 날짜, 형식 등) | 재계획하지 않고 안내 |
| NOT_FOUND | 등록되지 않은 도구 이름 | 재계획하지 않음 |
| NETWORK | 요청 실패(연결·시간 초과). 검증 스위치 `VERIFY_FAIL_TOOLS` 로 일부러 낸 실패도 이 코드 | 후보 조회면 같은 조건으로 1회 재시도, 보조 정보면 '불러오지 못했습니다'로 적고 계속 |
| HTTP / PORTAL | 공공데이터포털 오류 응답(인증·한도 초과 등) | 1회 재시도 후 안내 |
| PROVIDER | 제공기관 결과 코드가 00 이 아님 | 1회 재시도 후 안내 |
| PARSE | 응답 해석 실패 | 1회 재시도 후 안내 |
| INTERNAL | 내부 오류 (세부 내용은 화면에 내보내지 않음) | 안내 |

### 결과가 없을 때 · 값이 비어 있을 때

- 운항 목록이 0건이면 오류가 아니라 `count: 0` 과 `note`("조건에 맞는 운항은 0건")로 돌려줍니다. 받은 건수가 전체(total)보다 적으면 `note` 에 그 사실을 적습니다.
- 시설·매장 검색이 0건이면 `count: 0`, `empty: true`, `note: "결과 없음: …"` 입니다 (F3004).
- 판단에 필요한 값(출발 시각)이 비어 있는 편은 값을 지어내지 않고 **판단하지 않은 채 제외**하며 사유 '출발 시각 정보 없음(판단하지 않음)'을 남깁니다 (F3003). 탑승구·예상 시각처럼 보여 주기만 하는 값은 `null` 로 둡니다.
- 운항 자료는 오늘 −3일 ~ +6일만 있습니다. 그 밖의 날짜는 범위 안에서 **같은 요일**인 날의 운항표로 조회하고 `requested_date`, `date`, `date_estimated: true`, note('추정')로 요청과 조회 기준이 다르다는 사실을 밝힙니다 (F3002).

## 도구 목록
"""

TAIL = """
## 도구를 추가하는 방법

1. `app/tools/` 에 함수를 만들고 `register(Tool(name=..., description="언제 쓰는지", parameters={JSON 스키마}, func=..., kind=...))` 로 등록합니다. 잘못된 입력은 `ValueError`, 외부 API 실패는 `ApiError(code, message)` 로 올리면 레지스트리가 봉투로 바꿉니다.
2. `app/tools/__init__.py` 의 import 목록에 모듈을 추가합니다.
3. 에이전트가 쓰게 하려면 `app/agent/nodes.py` 의 `plan` 에서 의도에 맞게 단계를 넣고, `respond` 에 출력 문장을 추가합니다.
4. 이 파일의 META 에 역할·출력·실패 경우를 적고 `python docs/make_tools_doc.py` 로 정의서를 다시 만듭니다.
5. `tests/` 에 가짜 응답으로 정상·실패 테스트를 추가하고, `python verify/run_checks.py` 의 1번 항목으로 실제 호출을 확인합니다.
"""


def schema_rows(params):
    req = set(params.get("required") or [])
    out = ["| 입력 | 형식 | 필수 | 설명 |", "|---|---|---|---|"]
    for name, p in (params.get("properties") or {}).items():
        kind = p.get("type", "")
        if p.get("enum"):
            kind += f" ({' / '.join(p['enum'])})"
        out.append(f"| {name} | {kind} | {'예' if name in req else ''} | {p.get('description', '')} |")
    return out


def build():
    lines = [HEAD]
    for t in registry.catalog():
        role, output, failures = META.get(t["name"], ("", "", ""))
        lines += [f"### `{t['name']}` — {role}", "",
                  f"- 종류: {'외부 API 툴' if t['kind'] == 'external' else '로컬 서버 API 툴'}",
                  f"- 설명(에이전트가 보는 문장): {t['description']}", ""]
        lines += schema_rows(t["parameters"])
        lines += ["", f"- 출력: {output}", f"- 실패·빈 결과: {failures}", ""]
    lines.append(TAIL)
    return "\n".join(lines)


if __name__ == "__main__":
    path = os.path.join(ROOT, "docs", "TOOLS.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"썼습니다: {os.path.relpath(path, ROOT)} (도구 {len(registry.catalog())}개)")
