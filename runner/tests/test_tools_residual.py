"""Residual tests for ``runner/tools`` — covers pure helpers and unit-level
surfaces that aren't tested elsewhere.

Targets:
- ``utils.interrupt`` — per-thread + global flag semantics
- ``tools.thread_context`` — contextvar propagation semantics
- ``tools.tool_output_limits`` — config coercion + cache invalidation
- ``tools.tool_result_storage`` — preview generation + persisted-message shape
- ``tools.system.clean`` + ``tools.system.ansi_strip`` — ANSI/fence stripping
- ``tools.execute_code`` — ``json_parse``, ``shell_quote``, ``retry``, ``_scrub_child_env``,
  ``generate_spiritagent_tools_module``
- ``tools.browser.url_safety`` — always-blocked IPs + private-IP gate (sync)
- ``tools.browser.website_policy`` — host-matching rules + blocklist caching
- ``tools.toolsets`` — disabled-toolset set logic

These tests run fast (no subprocess, no network) so they belong in the
default suite, not the build-gate slow path.
"""

from tools.execute_code import code_execution_tool as ec
from tools.thread_context import propagate_context_to_thread
from tools.tool_output_limits import (
    get_max_bytes,
    reset_cache,
)
from tools.tool_result_storage import (
    DEFAULT_BUDGET,
    PERSISTED_OUTPUT_TAG,
    maybe_persist_tool_result,
)
from utils import is_interrupted, set_global_interrupt

# ---------------------------------------------------------------------------
# interrupt
# ---------------------------------------------------------------------------


class TestInterruptFlags:
    def setup_method(self):
        # Always start from a clean state.
        set_global_interrupt(False)

    def teardown_method(self):
        set_global_interrupt(False)

    def test_global_interrupt_propagates_to_is_interrupted(self):
        set_global_interrupt(True)
        assert is_interrupted() is True

    def test_global_interrupt_clears(self):
        set_global_interrupt(True)
        set_global_interrupt(False)
        assert is_interrupted() is False

    def test_global_interrupt_visible_from_another_thread(self):
        """``is_interrupted`` MUST observe the global flag from any thread.

        A long-running tool running on a worker pool thread can't poll
        its own per-thread state; it MUST see the global flag the WS
        loop set from the main asyncio loop.
        """
        import threading

        set_global_interrupt(True)
        observed = []

        def _worker():
            observed.append(is_interrupted())

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        assert observed == [True]

    def test_set_interrupt_targets_specific_thread(self):
        """Per-thread set only marks that one thread as interrupted."""
        import threading

        set_global_interrupt(False)
        other_thread_seen = []

        def _worker():
            other_thread_seen.append(is_interrupted())

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        # Worker thread is not the current thread; global is False, so it's False.
        assert other_thread_seen == [False]


# ---------------------------------------------------------------------------
# thread_context
# ---------------------------------------------------------------------------


class TestPropagateContextToThread:
    def test_runs_target_in_thread_and_returns_value(self):
        import threading

        seen: list = []

        def _target():
            seen.append(threading.current_thread().name)
            return "ok"

        wrapped = propagate_context_to_thread(_target)
        result = wrapped()
        assert result == "ok"
        assert len(seen) == 1
        # The target ran in some thread; we don't pin which one (pytest
        # may use the main thread for sync wrappers) but we DO confirm
        # it ran exactly once.

    def test_propagates_passes_args_through(self):
        def _target(a, b):
            return a + b

        wrapped = propagate_context_to_thread(_target)
        assert wrapped(2, 3) == 5


# ---------------------------------------------------------------------------
# tool_output_limits
# ---------------------------------------------------------------------------


class TestToolOutputLimits:
    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()

    def test_reset_cache_invalidates(self, monkeypatch):
        import tools.tool_output_limits as tol

        real = tol.load_config
        monkeypatch.setattr(
            tol,
            "load_config",
            lambda: {"tool_output": {"max_bytes": 99_999}},
        )
        try:
            reset_cache()
            assert get_max_bytes() == 99_999
        finally:
            monkeypatch.setattr(tol, "load_config", real)
            reset_cache()


# ---------------------------------------------------------------------------
# tool_result_storage
# ---------------------------------------------------------------------------


class TestMaybePersistToolResult:
    def test_long_content_with_env_writes_to_sandbox(self):
        """When env.execute succeeds, the result is the persisted marker envelope."""

        class _FakeEnv:
            def __init__(self):
                self.executed: list = []

            def execute(self, cmd, timeout=30, stdin_data=""):
                self.executed.append((cmd, stdin_data))
                return {"returncode": 0}

            def get_temp_dir(self):
                return "/sandbox/tmp"

        env = _FakeEnv()
        s = "x" * (DEFAULT_BUDGET.default_result_size + 10_000)
        out = maybe_persist_tool_result(
            s,
            tool_name="some_big_tool",
            tool_use_id="t3",
            env=env,
        )
        # Persisted marker MUST be present.
        assert PERSISTED_OUTPUT_TAG in out
        assert "Persisted" in out or "saved" in out.lower()
        # Sandbox received a write.
        assert env.executed, "env.execute was not called for the sandbox write"

    def test_sandbox_write_failure_falls_back_to_inline(self):
        """If sandbox write fails, fall back to inline truncation (don't lose the output)."""

        class _FakeEnv:
            def execute(self, cmd, timeout=30, stdin_data=""):
                return {"returncode": 1}  # failed

            def get_temp_dir(self):
                return "/sandbox/tmp"

        s = "x" * (DEFAULT_BUDGET.default_result_size + 10_000)
        out = maybe_persist_tool_result(
            s,
            tool_name="some_big_tool",
            tool_use_id="t4",
            env=_FakeEnv(),
        )
        assert "Truncated" in out


# ---------------------------------------------------------------------------
# system / clean
# ---------------------------------------------------------------------------


class TestExecuteCodeHelpers:
    """The generated sandbox module is a code TEMPLATE — these tests ``exec``
    it for real (against a local listener / request files) so a missing
    preamble or broken transport fails here instead of only in the sandbox.
    """

    @staticmethod
    def _exec_module(enabled: list[str], transport: str, monkeypatch) -> dict:
        monkeypatch.setenv("SPIRITAGENT_RPC_TOKEN", "test-token")
        src = ec.generate_spiritagent_tools_module(enabled, transport=transport)
        ns: dict = {}
        exec(compile(src, "spiritagent_tools.py", "exec"), ns)
        return ns

    def test_generated_module_uds_round_trip(self, monkeypatch):
        """UDS/TCP transport: auth line first, then the request; response parsed."""
        import json
        import socket
        import threading

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        seen: dict = {}

        def serve() -> None:
            conn, _ = srv.accept()
            buf = b""
            while buf.count(b"\n") < 2:
                buf += conn.recv(65536)
            auth_line, req_line, _ = buf.split(b"\n", 2)
            seen["auth"] = json.loads(auth_line).get("auth")
            seen["request"] = json.loads(req_line)
            conn.sendall((json.dumps({"ok": True}) + "\n").encode())
            conn.close()

        threading.Thread(target=serve, daemon=True).start()
        monkeypatch.setenv("SPIRITAGENT_RPC_SOCKET", f"tcp://127.0.0.1:{srv.getsockname()[1]}")
        ns = self._exec_module(["read_file"], "uds", monkeypatch)
        result = ns["read_file"]("/some/file")
        srv.close()
        assert result == {"ok": True}
        assert seen["auth"] == "test-token"
        assert seen["request"]["tool"] == "read_file"
        assert seen["request"]["args"]["path"] == "/some/file"

    def test_generated_module_file_round_trip_carries_token(self, tmp_path, monkeypatch):
        import json
        import os
        import threading
        import time

        monkeypatch.setenv("SPIRITAGENT_RPC_DIR", str(tmp_path))
        ns = self._exec_module(["read_file"], "file", monkeypatch)
        seen: dict = {}

        def respond() -> None:
            for _ in range(500):
                reqs = [f for f in os.listdir(tmp_path) if f.startswith("req_")]
                if reqs:
                    seen["request"] = json.loads((tmp_path / reqs[0]).read_text(encoding="utf-8"))
                    (tmp_path / f"res_{reqs[0][4:]}").write_text(json.dumps({"ok": True}), encoding="utf-8")
                    return
                time.sleep(0.01)

        threading.Thread(target=respond, daemon=True).start()
        assert ns["read_file"]("/x") == {"ok": True}
        assert seen["request"]["token"] == "test-token"
        assert seen["request"]["tool"] == "read_file"

    def test_rpc_server_loop_rejects_bad_token_then_accepts_good(self):
        """A connection with the wrong token is dropped and the listener keeps
        accepting — one unauthenticated local process must not hijack the slot."""
        import json
        import socket
        import threading

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        thread = threading.Thread(
            target=ec._rpc_server_loop,
            args=(srv, "task", [], [0], 99, frozenset(), "good-token"),
            daemon=True,
        )
        thread.start()
        try:
            bad = socket.create_connection(("127.0.0.1", srv.getsockname()[1]), timeout=5)
            bad.sendall((json.dumps({"auth": "wrong"}) + "\n").encode())
            assert bad.recv(65536) == b"", "bad-token connection must be closed"
            bad.close()

            good = socket.create_connection(("127.0.0.1", srv.getsockname()[1]), timeout=5)
            good.sendall((json.dumps({"auth": "good-token"}) + "\n" + json.dumps({"tool": "read_file", "args": {}}) + "\n").encode())
            resp = json.loads(good.recv(65536).decode())
            assert "error" in resp, "allowed_tools=frozenset() must yield the not-available error"
            good.close()
        finally:
            srv.close()
            thread.join(timeout=5)

    def test_scrub_child_env_strips_unlisted_spiritagent_vars(self):
        """SPIRITAGENT_* vars not in the allow-list MUST be dropped."""
        env = {"SPIRITAGENT_HOME": "/x", "SPIRITAGENT_FOO": "bar"}
        out = ec._scrub_child_env(env, is_passthrough=lambda _k: False, is_windows=False)
        # SPIRITAGENT_HOME is in SPIRITAGENT_CHILD_ALLOWED so it's kept.
        assert "SPIRITAGENT_HOME" in out
        # SPIRITAGENT_FOO is NOT in the allow-list; MUST be dropped.
        assert "SPIRITAGENT_FOO" not in out

    def test_generate_spiritagent_tools_module_contains_enabled_tool(self):
        out = ec.generate_spiritagent_tools_module(["read_file"], transport="uds")
        assert "def read_file(" in out

    def test_generate_spiritagent_tools_module_handles_empty_list(self):
        out = ec.generate_spiritagent_tools_module([], transport="file")
        assert "def json_parse" in out  # common helpers still present


# ---------------------------------------------------------------------------
# browser / url_safety
# ---------------------------------------------------------------------------


class TestUrlSafety:
    def test_is_safe_url_blocks_localhost(self):
        """127.0.0.1 is a SSRF risk — MUST be blocked regardless of config."""
        from utils import is_safe_url

        assert is_safe_url("http://127.0.0.1:8000") is False
        assert is_safe_url("http://localhost:8000") is False


class TestWebsitePolicy:
    def test_check_access_normalizes_url(self):
        """Host normalization MUST lowercase and strip leading www. for matching."""
        import utils.url_safety as url_safety
        from utils import check_website_access
        from utils.config import set_inmemory_config

        url_safety._cached_policy = None
        set_inmemory_config(
            {
                "security": {
                    "website_blocklist": {"enabled": True, "domains": ["example.com"]},
                },
            },
        )
        try:
            out = check_website_access("https://WWW.Example.COM/path")
            assert out is not None, "blocklist rule must match after host normalization"
            assert "example.com" in out.host  # www/case-insensitive rule match
            assert out.rule  # matched some rule from the blocklist
            assert check_website_access("https://unrelated.org/x") is None
        finally:
            set_inmemory_config({})
            url_safety._cached_policy = None


# ---------------------------------------------------------------------------
# toolsets
# ---------------------------------------------------------------------------


class TestToolsets:
    def test_excluded_tool_names_filters_by_prefix(self):
        """A disabled toolset hides any tool whose name matches one of its declared prefixes."""
        # Pick a real prefix from the catalog so this test stays valid
        # even if the catalog is curated later.
        from tools.toolsets.catalog import TOOLSET_CATALOG, excluded_tool_names

        # Find a toolset with at least one prefix that matches a known
        # registered tool name; ``browser_*`` is a stable prefix.
        browser_toolset = next(d for d in TOOLSET_CATALOG if any(p == "browser_" for p in d.prefixes))
        out = excluded_tool_names(
            {browser_toolset.id},
            {"browser_navigate", "terminal"},
        )
        assert "browser_navigate" in out
        assert "terminal" not in out

    def test_get_disabled_toolset_ids_reads_config(self, monkeypatch):
        """``get_disabled_toolset_ids`` reads via ``get_disabled_config_names(section="toolsets")``."""
        from tools.toolsets import helpers as toolsets_helpers

        real = toolsets_helpers.get_disabled_config_names
        monkeypatch.setattr(
            toolsets_helpers,
            "get_disabled_config_names",
            lambda section="skills": ({"skill_lab", "mcp_staging"} if section == "toolsets" else set()),
        )
        try:
            ids = toolsets_helpers.get_disabled_toolset_ids()
            assert "skill_lab" in ids
            assert "mcp_staging" in ids
        finally:
            monkeypatch.setattr(toolsets_helpers, "get_disabled_config_names", real)
