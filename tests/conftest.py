"""모든 테스트가 실제 data/ 폴더 대신 임시 SQLite 파일을 쓰게 한다 (사용자 기록 보호)."""
import pytest

from app import config


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PLAN_DB", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "logs"))
    # .env 에 GMS_API_KEY 가 있어도 테스트는 실제 LLM 을 부르지 않는다 (규칙 기반으로 결과를 고정)
    monkeypatch.setattr(config, "GMS_API_KEY", "")
    yield


@pytest.fixture(autouse=True)
def reset_traffic_state():
    from app.tools import traffic
    traffic._expect_state.update(path=None, fail_until=0.0, error=None)
    yield
