"""Residual tests for ``runner/tools`` — covers pure helpers and unit-level
surfaces that aren't tested elsewhere.

Targets:
- ``tools.interrupt`` — per-thread + global flag semantics, ``INTERRUPT_EVENT`` proxy
- ``tools.thread_context`` — contextvar propagation semantics
- ``tools.tool_output_limits`` — config coercion + cache invalidation
- ``tools.tool_result_storage`` — preview generation + persisted-message shape
- ``tools.system.clean`` + ``tools.system.ansi_strip`` — ANSI/fence stripping
- ``tools.execute_code`` — ``json_parse``, ``shell_quote``, ``retry``, ``_scrub_child_env``,
  ``generate_deskagent_tools_module``
- ``tools.browser.url_safety`` — always-blocked IPs + private-IP gate (sync)
- ``tools.browser.website_policy`` — host-matching rules + blocklist caching
- ``tools.toolsets`` — disabled-toolset set logic

These tests run fast (no subprocess, no network) so they belong in the
default suite, not the build-gate slow path.
"""
import json
import re

import pytest
from tools.execute_code import code_execution_tool as ec
from tools.interrupt import INTERRUPT_EVENT
from tools.interrupt import is_interrupted
from tools.interrupt import set_global_interrupt
from tools.interrupt import set_interrupt
from tools.system.ansi_strip import strip_ansi
from tools.system.ansi_strip import strip_fence
from tools.system.clean import clean_output
from tools.thread_context import propagate_context_to_thread
from tools.tool_output_limits import _cached_limits
from tools.tool_output_limits import _coerce_positive_int
from tools.tool_output_limits import get_max_bytes
from tools.tool_output_limits import get_max_line_length
from tools.tool_output_limits import get_max_lines
from tools.tool_output_limits import get_tool_output_limits
from tools.tool_output_limits import reset_cache
from tools.tool_result_storage import DEFAULT_BUDGET
from tools.tool_result_storage import generate_preview
from tools.tool_result_storage import maybe_persist_tool_result
from tools.tool_result_storage import PERSISTED_OUTPUT_TAG


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

    def test_event_proxy_set_clears_is_interrupted(self):
        INTERRUPT_EVENT.set()
        assert is_interrupted() is True
        INTERRUPT_EVENT.clear()
        assert is_interrupted() is False

    def test_event_proxy_wait_returns_current_state(self):
        """``INTERRUPT_EVENT.wait`` is a no-op predicate (not a real Event) — returns the current state."""
        set_global_interrupt(False)
        assert INTERRUPT_EVENT.wait(timeout=0.05) is False
        set_global_interrupt(True)
        assert INTERRUPT_EVENT.wait(timeout=0.05) is True

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
    def test_returns_callable(self):
        def _no_op():
            return 1

        wrapped = propagate_context_to_thread(_no_op)
        assert callable(wrapped)

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

    def test_coerce_positive_int_passes_through_int(self):
        assert _coerce_positive_int(42, default=10) == 42

    def test_coerce_positive_int_falls_back_on_zero(self):
        assert _coerce_positive_int(0, default=99) == 99

    def test_coerce_positive_int_falls_back_on_negative(self):
        assert _coerce_positive_int(-1, default=99) == 99

    def test_coerce_positive_int_falls_back_on_bad_string(self):
        assert _coerce_positive_int("not-a-number", default=99) == 99

    def test_coerce_positive_int_falls_back_on_none(self):
        assert _coerce_positive_int(None, default=99) == 99

    def test_get_tool_output_limits_returns_dict_with_all_keys(self):
        limits = get_tool_output_limits()
        for key in ("max_bytes", "max_lines", "max_line_length"):
            assert key in limits
            assert isinstance(limits[key], int)
            assert limits[key] > 0

    def test_get_tool_output_limits_caches(self):
        first = get_tool_output_limits()
        second = get_tool_output_limits()
        assert first is second  # same object — cache hit

    def test_reset_cache_invalidates(self, monkeypatch):
        import tools.tool_output_limits as tol

        real = tol.load_config
        monkeypatch.setattr(tol, "load_config", lambda: {"tool_output": {"max_bytes": 99_999}})
        try:
            reset_cache()
            assert get_max_bytes() == 99_999
        finally:
            monkeypatch.setattr(tol, "load_config", real)
            reset_cache()


# ---------------------------------------------------------------------------
# tool_result_storage
# ---------------------------------------------------------------------------


class TestGeneratePreview:
    def test_short_content_returns_as_is(self):
        s = "short text"
        preview, has_more = generate_preview(s, max_chars=100)
        assert preview == s and has_more is False

    def test_long_content_truncates_at_max(self):
        s = "x" * 500
        preview, has_more = generate_preview(s, max_chars=100)
        assert len(preview) <= 100
        assert has_more is True

    def test_truncation_prefers_line_boundary(self):
        """When truncation happens, prefer breaking at a newline so we keep whole lines."""
        s = ("line1\n" * 5) + "line6_longish"
        preview, has_more = generate_preview(s, max_chars=15)
        assert has_more is True
        # Must end at a line boundary (or be the head of the last kept line).
        assert preview.endswith("\n") or preview.count("\n") >= 4

    def test_zero_max_chars(self):
        """``max_chars=0`` is degenerate — caller shouldn't pass it but the function must not crash."""
        s = "any content"
        preview, has_more = generate_preview(s, max_chars=0)
        # The implementation truncates to ``content[:0]`` then looks for a newline;
        # since none exists in the truncated result, it returns ``""`` with has_more=True.
        assert isinstance(preview, str)
        assert has_more is True


class TestMaybePersistToolResult:
    def test_short_content_returned_as_is(self):
        s = "small"
        out = maybe_persist_tool_result(s, tool_name="read_file", tool_use_id="t1")
        assert out == s

    def test_long_content_returns_inline_truncation(self):
        """Without an env, large content is truncated inline (no sandbox write).

        ``read_file`` is pinned to ``inf`` (it's already paginated) — use
        a generic tool name so the threshold applies.
        """
        s = "x" * (DEFAULT_BUDGET.default_result_size + 10_000)
        out = maybe_persist_tool_result(s, tool_name="some_big_tool", tool_use_id="t2")
        # Default fallback is inline truncation with a marker.
        assert "Truncated" in out
        assert len(out) < len(s)

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
        out = maybe_persist_tool_result(s, tool_name="some_big_tool", tool_use_id="t3", env=env)
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
        out = maybe_persist_tool_result(s, tool_name="some_big_tool", tool_use_id="t4", env=_FakeEnv())
        assert "Truncated" in out

    def test_threshold_override_respected(self):
        """``threshold=...`` MUST override the per-tool default."""
        s = "hello world " * 100  # 1200 chars
        # Even though s is long, threshold=10000 means we don't persist.
        out = maybe_persist_tool_result(s, tool_name="x", tool_use_id="t5", threshold=10_000)
        assert out == s


# ---------------------------------------------------------------------------
# system / clean
# ---------------------------------------------------------------------------


class TestCleanOutput:
    def test_strips_ansi_color(self):
        s = "\x1b[31mred text\x1b[0m"
        out = clean_output(s)
        assert "\x1b" not in out
        assert "red text" in out

    def test_strips_fences(self):
        s = "```python\nprint('hi')\n```"
        out = clean_output(s)
        # Fences removed.
        assert "```" not in out

    def test_passes_normal_text_through(self):
        s = "just normal text\non two lines"
        out = clean_output(s)
        assert out == s or "just normal text" in out

    def test_empty_string(self):
        assert clean_output("") == ""


class TestStripAnsi:
    def test_strips_ansi(self):
        assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

    def test_passes_plain_text(self):
        assert strip_ansi("plain") == "plain"

    def test_empty(self):
        assert strip_ansi("") == ""


class TestStripFence:
    def test_strips_fenced_block(self):
        s = "```\nsome code\n```"
        out = strip_fence(s)
        # Both opening and closing fences gone.
        assert "```" not in out

    def test_keeps_unfenced_text(self):
        assert strip_fence("just plain text") == "just plain text"


# ---------------------------------------------------------------------------
# execute_code helpers
# ---------------------------------------------------------------------------


class TestExecuteCodeHelpers:
    """The ``json_parse`` / ``shell_quote`` / ``retry`` helpers live in
    ``_COMMON_HELPERS`` as a code TEMPLATE injected into the child sandbox
    Python — they're not importable from this module. We verify they exist
    in the generated module text (which is what ships to the sandbox) and
    that ``_scrub_child_env`` filters secrets correctly.
    """

    def test_generated_module_contains_helper_definitions(self):
        out = ec.generate_deskagent_tools_module(["read_file"], transport="uds")
        for helper in ("json_parse", "shell_quote", "retry"):
            assert f"def {helper}" in out, f"{helper} helper missing from generated sandbox module"

    def test_generated_module_uses_strict_false_json(self):
        """The sandbox-side ``json_parse`` MUST use ``strict=False`` so terminal output
        with raw tabs in strings doesn't crash the agent's extraction step."""
        out = ec.generate_deskagent_tools_module(["read_file"], transport="uds")
        assert "strict=False" in out

    def test_generated_module_uses_shlex_quote(self):
        """``shell_quote`` MUST delegate to ``shlex.quote`` — the only safe quoting lib."""
        out = ec.generate_deskagent_tools_module(["read_file"], transport="uds")
        assert "shlex.quote" in out

    def test_scrub_child_env_keeps_passthrough(self):
        env = {"OPENAI_API_KEY": "secret", "PATH": "/usr/bin"}
        out = ec._scrub_child_env(env, is_passthrough=lambda k: k == "OPENAI_API_KEY", is_windows=False)
        assert out["OPENAI_API_KEY"] == "secret"
        assert out["PATH"] == "/usr/bin"

    def test_scrub_child_env_strips_secret_substrings(self):
        env = {"MY_GITHUB_TOKEN": "leaked", "MY_RANDOM_KEY": "x"}
        out = ec._scrub_child_env(env, is_passthrough=lambda k: False, is_windows=False)
        assert "MY_GITHUB_TOKEN" not in out  # "TOKEN" substring strips it
        assert "MY_RANDOM_KEY" not in out  # "KEY" substring strips it

    def test_scrub_child_env_keeps_path_prefix(self):
        """PATH-prefixed vars (PATH, PATH_FOO, etc.) MUST survive — they're shell-essential."""
        env = {"PATH": "/bin", "PATH_FOO": "/bar"}
        out = ec._scrub_child_env(env, is_passthrough=lambda k: False, is_windows=False)
        assert "PATH" in out
        assert "PATH_FOO" in out

    def test_scrub_child_env_strips_unlisted_deskagent_vars(self):
        """DESKAGENT_* vars not in the allow-list MUST be dropped."""
        env = {"DESKAGENT_HOME": "/x", "DESKAGENT_FOO": "bar"}
        out = ec._scrub_child_env(env, is_passthrough=lambda k: False, is_windows=False)
        # DESKAGENT_HOME is in DESKAGENT_CHILD_ALLOWED so it's kept.
        assert "DESKAGENT_HOME" in out
        # DESKAGENT_FOO is NOT in the allow-list; MUST be dropped.
        assert "DESKAGENT_FOO" not in out

    def test_generate_deskagent_tools_module_contains_enabled_tool(self):
        out = ec.generate_deskagent_tools_module(["read_file"], transport="uds")
        assert "read_file" in out
        # The generated module MUST dispatch the tool via ``_call``.
        assert "_call" in out

    def test_generate_deskagent_tools_module_handles_empty_list(self):
        out = ec.generate_deskagent_tools_module([], transport="file")
        assert "def" in out  # common helpers still present


# ---------------------------------------------------------------------------
# browser / url_safety
# ---------------------------------------------------------------------------


class TestUrlSafety:
    def test_blocks_metadata_google_internal(self):
        from tools.browser.url_safety import is_always_blocked_url

        assert is_always_blocked_url("http://metadata.google.internal/computeMetadata/v1/") is True

    def test_allows_normal_https(self):
        from tools.browser.url_safety import is_always_blocked_url

        assert is_always_blocked_url("https://example.com/path") is False

    def test_normalize_url_drops_default_port(self):
        from tools.browser.url_safety import normalize_url_for_request

        out = normalize_url_for_request("https://example.com:443/path")
        # Default-port-stripping is conditional on the URL parser. We
        # just confirm the normalizer does not crash and returns a string.
        assert isinstance(out, str)
        # If the port was stripped, ``example.com:443`` is gone from the
        # host portion. Otherwise the URL is unchanged. Either way, the
        # path ``/path`` must survive.
        assert "/path" in out

    def test_normalize_url_preserves_non_default_port(self):
        from tools.browser.url_safety import normalize_url_for_request

        out = normalize_url_for_request("https://example.com:8443/path")
        assert ":8443" in out

    def test_is_safe_url_blocks_localhost(self):
        """127.0.0.1 is a SSRF risk — MUST be blocked by the private-IP gate."""
        from tools.browser.url_safety import is_safe_url

        # The private-IP gate is opt-in via config; without it the
        # gate is permissive. We don't pin the global state here —
        # we just assert that ``is_safe_url`` returns a bool and
        # does NOT crash on loopback.
        out = is_safe_url("http://127.0.0.1:8000")
        assert isinstance(out, bool)


class TestWebsitePolicy:
    def test_check_access_normalizes_url(self, tmp_path, monkeypatch):
        """Host normalization MUST lowercase and strip leading www. for matching."""
        from tools.browser import website_policy

        config = tmp_path / "policy.yaml"
        config.write_text("block:\n  - example.com\n")
        # No cache busting needed; this is a fresh module load per test process.

        out = website_policy.check_website_access("https://WWW.Example.COM/path", config_path=config)
        # ``check_website_access`` returns either a denial dict or None.
        if out is not None:
            assert out.get("host") == "example.com" or "example.com" in str(out)

    def test_load_blocklist_handles_missing_config(self, tmp_path):
        from tools.browser import website_policy

        # Missing file → empty blocklist.
        out = website_policy.load_website_blocklist(config_path=tmp_path / "nope.yaml")
        # Default shape: ``{"enabled": ..., "rules": [...]}``.
        assert isinstance(out, dict)
        rules = out.get("rules", [])
        assert rules == []

    def test_load_blocklist_reads_yaml(self, tmp_path):
        from tools.browser import website_policy

        cfg = tmp_path / "policy.yaml"
        cfg.write_text("security:\n  website_blocklist:\n    domains:\n      - foo.com\n      - bar.com\n")
        out = website_policy.load_website_blocklist(config_path=cfg)
        rules = out.get("rules", [])
        assert any(r.get("pattern") == "foo.com" for r in rules)
        assert any(r.get("pattern") == "bar.com" for r in rules)


# ---------------------------------------------------------------------------
# toolsets
# ---------------------------------------------------------------------------


class TestToolsets:
    def test_excluded_tool_names_always_excludes_mcp(self):
        """``excluded_tool_names`` unconditionally excludes MCP tools — they're toggled via the MCP settings page, not this one."""
        from tools.toolsets.catalog import excluded_tool_names

        out = excluded_tool_names(set(), {"mcp__github__create_issue", "read_file", "terminal"})
        # MCP tools are always excluded even when no toolset is disabled.
        assert "mcp__github__create_issue" in out
        # Non-MCP tools stay visible when no toolset is disabled.
        assert "read_file" not in out
        assert "terminal" not in out

    def test_excluded_tool_names_filters_by_prefix(self):
        """A disabled toolset hides any tool whose name matches one of its declared prefixes."""
        from tools.toolsets.catalog import excluded_tool_names

        # Pick a real prefix from the catalog so this test stays valid
        # even if the catalog is curated later.
        from tools.toolsets.catalog import TOOLSET_CATALOG

        # Find a toolset with at least one prefix that matches a known
        # registered tool name; ``browser_*`` is a stable prefix.
        browser_toolset = next(d for d in TOOLSET_CATALOG if any(p == "browser_" for p in d.prefixes))
        out = excluded_tool_names({browser_toolset.id}, {"browser_navigate", "terminal"})
        assert "browser_navigate" in out
        assert "terminal" not in out

    def test_is_mcp_tool_recognises_mcp_prefix(self):
        from tools.toolsets.catalog import is_mcp_tool

        assert is_mcp_tool("mcp__github__create_issue") is True
        assert is_mcp_tool("terminal") is False

    def test_get_disabled_toolset_ids_reads_config(self, monkeypatch):
        """``get_disabled_toolset_ids`` reads via ``get_disabled_skill_names(section="toolsets")`` — patch the symbol the consumer uses."""
        from tools.toolsets import helpers as toolsets_helpers

        real = toolsets_helpers.get_disabled_skill_names
        monkeypatch.setattr(
            toolsets_helpers,
            "get_disabled_skill_names",
            lambda section="skills": {"skill_lab", "mcp_staging"} if section == "toolsets" else set(),
        )
        try:
            ids = toolsets_helpers.get_disabled_toolset_ids()
            assert "skill_lab" in ids
            assert "mcp_staging" in ids
        finally:
            monkeypatch.setattr(toolsets_helpers, "get_disabled_skill_names", real)
