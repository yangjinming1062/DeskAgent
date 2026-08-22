import pytest

STARTUP_IMPORTS = [
    ("websockets", "import websockets"),
    ("runner_version", "from runner_version import __version__"),
    (
        "tools (top-level facade)",
        "from tools import discover_builtin_tools, registry, ToolError",
    ),
    ("tools.audio (audio tools + STT/TTS)", "from tools.multimodal import audio"),
    (
        "tools.files.file_tools",
        "from tools.files.file_tools import reset_max_read_chars_cache",
    ),
    (
        "utils.interrupt",
        "from utils import set_global_interrupt, set_interrupt",
    ),
    (
        "tools.mcp.discover_mcp_tools (load-bearing)",
        "from tools.mcp import discover_mcp_tools",
    ),
    (
        "tools.mcp.mcp_tool.reload_mcp_servers (load-bearing)",
        "from tools.mcp.mcp_tool import reload_mcp_servers",
    ),
    ("tools.tool_output_limits", "from tools.tool_output_limits import reset_cache"),
    ("tools.toolsets", "from tools.toolsets import get_disabled_toolset_ids"),
    (
        "tools.system.activity + activity_tools",
        "from tools.system import activity, activity_tools",
    ),
    ("utils.core", "from utils import pid_exists, set_handler"),
    ("utils.capabilities", "from utils.capabilities import snapshot"),
    ("utils.constants", "from utils.constants import get_spiritagent_home"),
]


@pytest.mark.parametrize(
    "desc,stmt",
    STARTUP_IMPORTS,
    ids=[desc for desc, _ in STARTUP_IMPORTS],
)
def test_server_startup_import(desc: str, stmt: str) -> None:
    """Each row is consolidated from a contiguous block of
    ``server.py`` (the import lines collapse to discrete rows when
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
    most of the imports thanks to the parametrized rows above. The
    real cost is the second-stage traversal of those cached modules
    plus any ``__init__.py`` side effects (e.g. ``tools/system/__init__``
    registering the four ``system.*`` tools)."""
    import server  # noqa: F401 — side-effect import test
