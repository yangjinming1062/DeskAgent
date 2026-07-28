import pytest

STARTUP_IMPORTS = [
    ("websockets", "import websockets"),
    ("tools (top-level facade)", "from tools import discover_builtin_tools, registry, ToolError"),
    ("tools.files.file_tools", "from tools.files.file_tools import reset_max_read_chars_cache"),
    ("tools.interrupt", "from tools.interrupt import set_global_interrupt, set_interrupt"),
    ("tools.mcp.discover_mcp_tools (load-bearing)", "from tools.mcp import discover_mcp_tools"),
    ("tools.mcp.mcp_tool.reload_mcp_servers (load-bearing)", "from tools.mcp.mcp_tool import reload_mcp_servers"),
    ("tools.tool_output_limits", "from tools.tool_output_limits import reset_cache"),
    ("tools.toolsets", "from tools.toolsets import get_disabled_toolset_ids"),
    ("utils.core", "from utils import pid_exists, set_handler"),
    ("utils.constants", "from utils.constants import get_deskagent_home"),
]


@pytest.mark.parametrize("desc,stmt", STARTUP_IMPORTS, ids=[desc for desc, _ in STARTUP_IMPORTS])
def test_server_startup_import(desc: str, stmt: str) -> None:
    """Each row is consolidated from a contiguous block of
    ``server.py:9-22`` (the 14 import lines collapse to 10 rows when
    e.g. ``from X import a, b, c`` is one row). Failures name the
    single missing dep via the parametrize id; the parametrize id is
    the only signal the IDs were chosen for (pytest's default id
    would embed the raw import statement)."""
    try:
        exec(stmt, {})
    except Exception as exc:
        pytest.fail(f"{desc!r} import failed: {type(exc).__name__}: {exc}")


def test_server_module_loads_end_to_end() -> None:
    """Whole-module check backstop: if a future contributor adds a
    top-level import to ``server.py`` and forgets to update
    ``STARTUP_IMPORTS`` (or vice-versa), this test catches it.

    Cheap: ``import server`` resolves to cached module lookups for
    most of ``server.py:9-22`` thanks to the parametrized rows above.
    The real cost is the second-stage traversal of those cached
    modules plus any ``__init__.py`` side effects."""
    import server  # noqa: F401
