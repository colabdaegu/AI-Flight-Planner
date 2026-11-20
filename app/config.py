"""설정. 키 값은 이 파일에 적지 않고 .env 에서만 읽는다 (NF3001)."""
import os

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_KEY", "").strip()
GMS_API_KEY = os.getenv("GMS_API_KEY", "").strip()
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

GMS_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"
GMS_MODEL = "gpt-5.4-nano"

DATA_DIR = os.path.join(ROOT, "data")
PLAN_DB = os.path.join(DATA_DIR, "planner.sqlite3")
LOG_DIR = os.path.join(DATA_DIR, "logs")

REPLAN_LIMIT = 3       # 명세 F3009 · NF3004
DATA_DAYS_BEFORE = 3   # 운항 공공데이터가 주는 날짜 범위: 오늘 −3일 ~ +6일 (F3002, 범위 밖은 같은 요일로 추정)
DATA_DAYS_AFTER = 6

# ---- 검증용 스위치 (셀프 체크리스트 7 · 15 · 17 · 22번). 평소에는 비워 둔다.
# VERIFY_FAIL_TOOLS=get_airport_congestion,get_incheon_passenger_forecast  → 그 도구를 일부러 실패시킨다(NETWORK)
# VERIFY_LOGGER=off     → 로깅 Agent 를 연결하지 않는다 (메인 Agent 단독 동작 확인)
# VERIFY_LOGGER=broken  → 로그 저장이 항상 실패하는 구독자를 단다 (저장 실패에도 대화가 되는지 확인)
VERIFY_FAIL_TOOLS = {t.strip() for t in os.getenv("VERIFY_FAIL_TOOLS", "").split(",") if t.strip()}
VERIFY_LOGGER = os.getenv("VERIFY_LOGGER", "on").strip().lower() or "on"
CACHE_TTL_SEC = 300    # 공공데이터 호출 한도 보호 (동일 요청 재사용)
HTTP_TIMEOUT = 15
