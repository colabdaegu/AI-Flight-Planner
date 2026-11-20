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
CACHE_TTL_SEC = 300    # 공공데이터 호출 한도 보호 (동일 요청 재사용)
HTTP_TIMEOUT = 15
