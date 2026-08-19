# 让 pytest 自动把 ``runner/`` 加入 ``sys.path``, 这样 ``runner/tests/`` 下的测试可以直接 ``import server``,
# 而不必在 pyproject.toml 里设 ``[tool.pytest.ini_options] pythonpath = ["."]``。
#
# Session 级 autouse: 工具发现流程只跑一次, 任何触及 registry / schema 面的测试都能看到已填充的单例。
# 幂等 — registry._tool_handlers 按名字索引, 重复发现是 no-op。
import pytest

from tools import discover_builtin_tools


@pytest.fixture(scope="session", autouse=True)
def _ensure_tools_discovered() -> None:
    discover_builtin_tools()
    yield
