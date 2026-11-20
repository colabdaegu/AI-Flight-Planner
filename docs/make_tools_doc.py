"""docs/TOOLS.md 를 다시 만들 때 쓰는 안내용 스크립트: 등록된 도구 목록을 출력한다.
실행: 프로젝트 루트에서  python docs/make_tools_doc.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import tools  # noqa: E402,F401
from app.tools import registry  # noqa: E402

print(json.dumps(registry.catalog(), ensure_ascii=False, indent=2))
