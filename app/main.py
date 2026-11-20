"""FastAPI 진입점. 실행: 프로젝트 루트에서  uvicorn app.main:app --reload

- /api/* : 백엔드 API
- / : web/ 정적 화면 (같은 주소에서 열리므로 CORS 가 필요 없다. 다른 주소에서 열 때만 허용)
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import tools  # noqa: F401  도구 등록
from app.api.routes import router
from app.logger_agent import attach_logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="항공 여행 일정 플래너")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
attach_logger()
app.mount("/", StaticFiles(directory=os.path.join(ROOT, "web"), html=True), name="web")
