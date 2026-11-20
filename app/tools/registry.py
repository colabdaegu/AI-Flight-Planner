"""도구 레지스트리 (명세 Tool 정의: name, description, parameters, func).

모든 도구 결과는 ok/fail 봉투로 돌아온다. 예외 문구는 화면에 그대로 내보내지 않는다.
"""
from dataclasses import dataclass
from typing import Callable

from app.tools.common import ApiError, fail, ok, scrub


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable
    kind: str = "external"      # external: 외부 API 도구, local: 이 서버 기능을 감싼 도구

    def run(self, **kwargs):
        try:
            return ok(self.func(**kwargs))
        except ApiError as e:
            return fail(e.code, e.message)
        except (TypeError, ValueError) as e:
            return fail("BAD_ARGS", scrub(e))
        except Exception as e:                       # 내부 오류: 세부 내용은 숨긴다
            print("[tool-error]", self.name, scrub(e))
            return fail("INTERNAL", "내부 오류가 발생했습니다.")


_TOOLS = {}


def register(tool):
    _TOOLS[tool.name] = tool
    return tool


def catalog():
    return [{"name": t.name, "kind": t.kind, "description": t.description, "parameters": t.parameters}
            for t in _TOOLS.values()]


def call(name, **kwargs):
    tool = _TOOLS.get(name)
    if tool is None:
        return fail("NOT_FOUND", f"알 수 없는 도구: {name}")
    return tool.run(**kwargs)
