# Lets pytest auto-add ``runner/`` to ``sys.path`` so ``import server``
# resolves from ``runner/tests/`` without a
# ``[tool.pytest.ini_options] pythonpath = ["."]`` special case.
#
# Session-scoped autouse: run tool discovery exactly once so any test that
# touches the registry / schema surface sees a populated singleton. Idempotent
# — registry._tool_handlers is keyed on name, so re-discovery is a no-op.
import pytest
from tools import discover_builtin_tools


@pytest.fixture(scope="session", autouse=True)
def _ensure_tools_discovered():
    discover_builtin_tools()
    yield
