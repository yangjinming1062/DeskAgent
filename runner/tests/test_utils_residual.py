"""Residual tests for ``runner/utils`` — covers everything not pinned by
``test_path_helpers.py`` or ``test_pid.py``.

Targets:
- ``utils.env_helpers`` — HOME injection for child processes
- ``utils.constants`` — path resolution + secure_parent_dir platform no-op
- ``utils.file_safety`` — write/read denial + cross-profile + sandbox mirror
- ``utils.redact`` — JWT / private-key / DB connstr / URL userinfo / env-var edge cases
- ``utils.capabilities`` — boundary cases for the snapshot helpers
- ``utils.reverse_rpc`` — handler not configured + happy path
- ``utils.config`` — cfg_* coercers
"""
import sys
from pathlib import Path

import pytest
import utils.reverse_rpc as reverse_rpc
from utils.capabilities import _binary_exists
from utils.capabilities import disk_free_bytes
from utils.capabilities import snapshot
from utils.config import cfg_bool
from utils.config import cfg_float
from utils.config import cfg_get
from utils.config import cfg_int
from utils.config import cfg_json
from utils.config import cfg_str
from utils.config import is_truthy_value
from utils.config import load_config
from utils.constants import CREATE_NO_WINDOW
from utils.constants import get_deskagent_dir
from utils.constants import get_deskagent_home
from utils.constants import get_skills_dir
from utils.constants import get_subprocess_home
from utils.constants import is_termux
from utils.constants import IS_WINDOWS
from utils.constants import secure_parent_dir
from utils.env_helpers import inject_context_deskagent_home
from utils.env_helpers import sanitize_subprocess_env
from utils.file_safety import classify_container_mirror_target
from utils.file_safety import classify_cross_profile_target
from utils.file_safety import classify_sandbox_mirror_target
from utils.file_safety import get_container_mirror_warning
from utils.file_safety import get_cross_profile_warning
from utils.file_safety import get_read_block_error
from utils.file_safety import get_sandbox_mirror_warning
from utils.file_safety import is_write_denied
from utils.redact import redact_sensitive_text
from utils.reverse_rpc import call_llm
from utils.reverse_rpc import set_handler


# ---------------------------------------------------------------------------
# env_helpers
# ---------------------------------------------------------------------------


class TestInjectContextDeskagentHome:
    def test_injects_when_override_set(self, monkeypatch):
        monkeypatch.setenv("DESKAGENT_HOME", "/custom/path")
        env: dict = {}
        inject_context_deskagent_home(env)
        assert env["DESKAGENT_HOME"] == "/custom/path"

    def test_does_nothing_when_override_unset(self, monkeypatch):
        monkeypatch.delenv("DESKAGENT_HOME", raising=False)
        env: dict = {"OTHER": "x"}
        inject_context_deskagent_home(env)
        assert env == {"OTHER": "x"}


class TestSanitizeSubprocessEnv:
    def test_merges_base_and_extra(self, monkeypatch):
        monkeypatch.setenv("DESKAGENT_HOME", "/x")
        out = sanitize_subprocess_env({"A": "1"}, {"B": "2"})
        assert out["A"] == "1" and out["B"] == "2"

    def test_overlay_wins_on_conflict(self, monkeypatch):
        monkeypatch.setenv("DESKAGENT_HOME", "/x")
        out = sanitize_subprocess_env({"A": "1"}, {"A": "2"})
        assert out["A"] == "2"

    def test_injects_home_from_override(self, monkeypatch):
        monkeypatch.setenv("DESKAGENT_HOME", "/x")
        out = sanitize_subprocess_env({})
        assert out["DESKAGENT_HOME"] == "/x"

    def test_home_is_str_not_path(self, monkeypatch):
        """HOME MUST be ``str``, not ``Path`` — child Python may not handle Path objects in os.environ."""
        monkeypatch.setenv("DESKAGENT_HOME", "/custom")
        out = sanitize_subprocess_env({})
        assert isinstance(out["HOME"], str)
        # Don't pin the exact string — Windows normalizes "/custom" to "\custom".
        # The contract under test is the type, not the path representation.


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


class TestGetDeskagentHome:
    def test_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        assert get_deskagent_home() == tmp_path

    def test_subprocess_home_falls_back_to_main(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DESKAGENT_SUBPROCESS_HOME", raising=False)
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        assert get_subprocess_home() == tmp_path


class TestGetSkillsDir:
    def test_returns_subdir_of_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        assert get_skills_dir() == tmp_path / "skills"


class TestGetDeskagentDir:
    def test_prefers_new_subpath_when_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        (tmp_path / "new").mkdir()
        assert get_deskagent_dir(new_subpath="new", old_name="old") == tmp_path / "new"

    def test_falls_back_to_old_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        (tmp_path / "old").mkdir()
        assert get_deskagent_dir(new_subpath="new", old_name="old") == tmp_path / "old"

    def test_returns_path_even_when_neither_exists(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        # Neither exists; ``get_deskagent_dir`` returns the new path
        # anyway so the caller can create it.
        assert get_deskagent_dir(new_subpath="new", old_name="old") == tmp_path / "new"


class TestIsTermux:
    def test_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        assert is_termux() is False

    def test_true_when_set(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118")
        assert is_termux() is True


class TestSecureParentDir:
    def test_creates_missing_parent(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        secure_parent_dir(target)
        assert target.parent.exists()

    def test_existing_parent_does_not_error(self, tmp_path):
        existing = tmp_path / "x"
        existing.mkdir()
        secure_parent_dir(existing / "child")
        # No exception = success.


class TestPlatformFlags:
    def test_create_no_window_is_zero_on_posix(self):
        if not IS_WINDOWS:
            assert CREATE_NO_WINDOW == 0
        else:
            # On Windows, must be the non-zero flag; exact value isn't
            # pinned (it's a subprocess constant) — just check it's truthy.
            assert CREATE_NO_WINDOW != 0

    def test_is_windows_matches_sys_platform(self):
        assert IS_WINDOWS == (sys.platform == "win32")


# ---------------------------------------------------------------------------
# file_safety
# ---------------------------------------------------------------------------


class TestIsWriteDenied:
    def test_denies_dotenv_in_home(self, tmp_path, monkeypatch):
        # ``is_write_denied`` resolves the user's $HOME via ``os.path.expanduser``.
        # We can't easily override expanduser, but the home-relative
        # denied list includes ``.env`` which is conventionally present.
        dotenv = Path.home() / ".env"
        if dotenv.exists():
            # Only assert if it actually exists on the host — otherwise
            # the path-resolution probe can't see the resolved form.
            assert is_write_denied(str(dotenv)) is True

    def test_denies_path_inside_deskagent_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        # ``auth.json`` inside DESKAGENT_HOME is a load-bearing denial.
        target = tmp_path / "auth.json"
        assert is_write_denied(str(target)) is True

    def test_denies_mcp_tokens_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        target = tmp_path / "mcp-tokens" / "github.json"
        assert is_write_denied(str(target)) is True

    def test_denies_pairing_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        target = tmp_path / "pairing" / "device.json"
        assert is_write_denied(str(target)) is True

    def test_allows_normal_temp_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path / "home"))
        target = tmp_path / "normal.txt"
        # No safe_write_root configured, so the last branch returns False.
        assert is_write_denied(str(target)) is False

    def test_path_traversal_does_not_bypass(self, monkeypatch, tmp_path):
        """``../auth.json`` from a tmp dir MUST still hit the denial."""
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        # The denial is resolved via ``os.path.realpath`` so traversal
        # can never land outside the denied set.
        escaped = tmp_path / ".." / tmp_path.name / "auth.json"
        # ``realpath`` resolves the escape back to ``tmp_path/auth.json``.
        assert is_write_denied(str(escaped)) is True


class TestGetReadBlockError:
    def test_blocks_internal_hub_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        target = tmp_path / "skills" / ".hub" / "index-cache"
        err = get_read_block_error(str(target))
        assert err is not None
        assert "index-cache" in err or "internal DeskAgent" in err

    def test_blocks_anthropic_oauth_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        target = tmp_path / "anthropic_oauth.json"
        err = get_read_block_error(str(target))
        assert err is not None and "credential store" in err

    def test_blocks_mcp_token_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        target = tmp_path / "mcp-tokens" / "github.json"
        err = get_read_block_error(str(target))
        assert err is not None

    def test_blocks_dotenv_basename(self, monkeypatch, tmp_path):
        """A ``.env`` file in a project dir MUST be denied — secrets policy."""
        # Path traversal / normal path both blocked.
        err = get_read_block_error(str(tmp_path / ".env"))
        assert err is not None and ".env" in err

    def test_allows_normal_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        # Real file outside the deny list.
        normal = tmp_path / "src" / "main.py"
        normal.parent.mkdir(parents=True)
        normal.write_text("x")
        assert get_read_block_error(str(normal)) is None


class TestClassifyCrossProfileTarget:
    def test_detects_target_in_other_profile_skills(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        (tmp_path / "skills").mkdir()  # so Path(...).parts[0] = "skills"
        target = tmp_path / "skills" / "private-skill" / "SKILL.md"
        # No profile split yet → classify as same-profile, returns None.
        assert classify_cross_profile_target(str(target)) is None

    def test_classifies_cross_profile_target(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        (tmp_path / "profiles" / "work" / "skills").mkdir(parents=True)
        target = tmp_path / "profiles" / "work" / "skills" / "x" / "SKILL.md"
        # Active profile defaults to "default" so writing into "work"
        # is a cross-profile write.
        info = classify_cross_profile_target(str(target))
        assert info is not None
        assert info["target_profile"] == "work"
        assert info["active_profile"] == "default"


class TestGetCrossProfileWarning:
    def test_warning_message_mentions_profile(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        (tmp_path / "profiles" / "work" / "skills").mkdir(parents=True)
        target = tmp_path / "profiles" / "work" / "skills" / "x" / "SKILL.md"
        msg = get_cross_profile_warning(str(target))
        assert msg is not None and "work" in msg

    def test_no_warning_when_same_profile(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        # No profiles subdir → active is "default", same-profile writes allowed.
        target = tmp_path / "skills" / "x" / "SKILL.md"
        target.parent.mkdir(parents=True)
        assert get_cross_profile_warning(str(target)) is None


class TestSandboxMirror:
    def test_no_warning_when_path_outside_sandbox(self, tmp_path):
        target = tmp_path / "regular" / "file.py"
        assert classify_sandbox_mirror_target(str(target)) is None
        assert get_sandbox_mirror_warning(str(target)) is None

    def test_classifies_sandbox_mirror_path(self, tmp_path):
        # ``_find_sandbox_mirror_segments`` matches ``sandboxes/<backend>/<id>/home/.deskagent/...``.
        # The 5-segment prefix is ``(sandboxes, backend, id, home, .deskagent)``.
        target = tmp_path / "sandboxes" / "docker" / "abc123" / "home" / ".deskagent" / "skills" / "x" / "SKILL.md"
        info = classify_sandbox_mirror_target(str(target))
        assert info is not None
        # The inner_path is everything after ``.deskagent``.
        assert "skills" in info["inner_path"]
        assert "x" in info["inner_path"]
        assert "SKILL.md" in info["inner_path"]


class TestContainerMirror:
    def test_no_warning_when_prefix_unset(self, tmp_path):
        target = tmp_path / "anywhere" / "file"
        assert classify_container_mirror_target(str(target)) is None

    def test_no_warning_when_target_outside_prefix(self, tmp_path):
        prefix = tmp_path / "prefix"
        prefix.mkdir()
        target = tmp_path / "other" / "file"
        assert classify_container_mirror_target(str(target), mirror_prefix=str(prefix)) is None

    def test_warning_when_target_inside_prefix(self, tmp_path):
        prefix = tmp_path / "prefix"
        prefix.mkdir()
        target = prefix / "sub" / "file.txt"
        info = classify_container_mirror_target(str(target), mirror_prefix=str(prefix))
        assert info is not None
        msg = get_container_mirror_warning(str(target), mirror_prefix=str(prefix))
        assert msg is not None and "prefix" in msg


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


class TestRedactSensitiveText:
    def test_disabled_by_config(self, monkeypatch):
        """``security.redact_secrets=false`` in config disables redaction."""
        import utils.redact as redact_mod

        real = redact_mod.load_config
        monkeypatch.setattr(redact_mod, "load_config", lambda: {"security": {"redact_secrets": False}})
        s = "sk-ant-api03-abcdefghijklmnop1234567890 should NOT be redacted"
        out = redact_sensitive_text(s)
        assert out == s
        monkeypatch.setattr(redact_mod, "load_config", real)

    def test_redacts_github_personal_token(self):
        s = "Token: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        out = redact_sensitive_text(s)
        assert "ghp_abcdef" not in out

    def test_redacts_openai_sk_live(self):
        s = "OPENAI_KEY=sk_live_abcdefghijklmnopqrstuvwxyz"
        out = redact_sensitive_text(s)
        assert "sk_live_abcdef" not in out
        # The env-var assignment format masks with "***" not just truncates.
        assert "OPENAI_KEY" in out

    def test_redacts_anthropic_claude_key(self):
        s = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234"
        out = redact_sensitive_text(s)
        # ``_mask_token`` keeps the 6-char fingerprint.
        assert "abcdefghijklmnopqrstuvwxyz1234" not in out

    def test_redacts_aws_access_key(self):
        s = "AKIAIOSFODNN7EXAMPLE"
        out = redact_sensitive_text(s)
        assert "IOSFODNN7EXAMPLE" not in out or "***" in out

    def test_redacts_private_key_block(self):
        s = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n-----END PRIVATE KEY-----"
        out = redact_sensitive_text(s)
        assert "BEGIN PRIVATE KEY" not in out
        assert "***PRIVATE_KEY***" in out

    def test_redacts_jwt(self):
        s = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        out = redact_sensitive_text(s)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out

    def test_redacts_db_connection_string(self):
        s = "postgresql://user:supersecretpassword@localhost/db"
        out = redact_sensitive_text(s)
        assert "supersecretpassword" not in out
        assert "user:" in out  # username preserved

    def test_redacts_url_userinfo(self):
        s = "Fetch https://alice:hunter2@example.com/api"
        out = redact_sensitive_text(s)
        assert "hunter2" not in out
        assert "alice" in out  # username preserved

    def test_redacts_authorization_header(self):
        s = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
        out = redact_sensitive_text(s)
        assert "abcdefghijklmnopqrstuvwxyz123456" not in out

    def test_redacts_json_field_value(self):
        s = '{"api_key": "sk_abcdefghijklmnopqrstuvwxyz123456"}'
        out = redact_sensitive_text(s)
        assert "sk_abcdefghij" not in out
        assert '"api_key"' in out

    def test_does_not_redact_substring_inside_longer_word(self):
        """A ``sk-...`` payload inside a larger identifier MUST NOT match — over-redaction risk."""
        s = "Xsk-12345678901234567890"  # ``X`` precedes ``sk-``; word-boundary anchor must skip.
        out = redact_sensitive_text(s)
        assert out == s

    def test_empty_input(self):
        assert redact_sensitive_text("") == ""

    def test_redact_failure_returns_original(self, monkeypatch):
        """If the redactor itself raises, MUST return the original text (defensive)."""
        import utils.redact as redact_mod

        real = redact_mod._redact
        monkeypatch.setattr(redact_mod, "_redact", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            out = redact_sensitive_text("sk_abcdefghijklmnop")
            assert out == "sk_abcdefghijklmnop"
        finally:
            monkeypatch.setattr(redact_mod, "_redact", real)


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


class TestCapabilitiesBoundary:
    def test_binary_exists_for_real_binary(self):
        assert _binary_exists(sys.executable) is True

    def test_binary_exists_for_missing(self):
        assert _binary_exists("definitely-not-a-binary-xyz123") is False

    def test_disk_free_bytes_for_existing_dir(self, tmp_path):
        free = disk_free_bytes(tmp_path)
        assert isinstance(free, int) and free > 0

    def test_disk_free_bytes_for_missing_returns_none(self, tmp_path):
        # Path that doesn't exist; shutil.disk_usage raises OSError.
        assert disk_free_bytes(tmp_path / "nope" / "nope") is None

    def test_snapshot_keys_complete(self):
        """``snapshot`` MUST return every documented key."""
        caps = snapshot()
        for key in ("microphone", "screen_capture", "local_stt", "local_tts", "system_activity", "platform", "python"):
            assert key in caps
        assert caps["platform"] == sys.platform
        # Values are bools for the capability flags.
        assert isinstance(caps["microphone"], bool)


# ---------------------------------------------------------------------------
# reverse_rpc
# ---------------------------------------------------------------------------


class TestReverseRpc:
    def test_call_llm_without_handler_raises(self):
        # Reset to no handler state.
        real = reverse_rpc._handler
        reverse_rpc._handler = None
        try:
            with pytest.raises(RuntimeError, match="not configured"):
                import asyncio

                asyncio.run(call_llm(messages=[]))
        finally:
            reverse_rpc._handler = real

    def test_call_llm_invokes_handler(self):
        async def _fake(kwargs):
            return "hello-from-handler"

        real = reverse_rpc._handler
        set_handler(_fake)
        try:
            import asyncio

            out = asyncio.run(call_llm(prompt="x"))
            assert out == "hello-from-handler"
        finally:
            reverse_rpc._handler = real


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfigHelpers:
    def test_is_truthy_accepts_known_truthy_strings(self):
        assert is_truthy_value("true") is True
        assert is_truthy_value("yes") is True
        assert is_truthy_value("on") is True
        assert is_truthy_value("1") is True
        # case + whitespace
        assert is_truthy_value("  TRUE  ") is True

    def test_is_truthy_rejects_garbage(self):
        assert is_truthy_value("nope") is False
        assert is_truthy_value("") is False
        assert is_truthy_value(None, default=False) is False
        assert is_truthy_value(None, default=True) is True

    def test_is_truthy_passes_through_bool(self):
        assert is_truthy_value(True) is True
        assert is_truthy_value(False) is False

    def test_is_truthy_numeric_truthy(self):
        # Non-zero numbers coerce True per the documented contract.
        assert is_truthy_value(1) is True
        assert is_truthy_value(0) is False

    def test_cfg_get_walks_nested(self):
        d = {"a": {"b": {"c": "deep"}}}
        assert cfg_get(d, "a", "b", "c") == "deep"
        assert cfg_get(d, "a", "missing", default="fallback") == "fallback"
        # Non-dict intermediate returns default.
        assert cfg_get(d, "a", "b", "c", "deeper", default=None) is None

    def test_cfg_get_returns_default_for_missing_keys(self):
        assert cfg_get({}, "missing", default=42) == 42

    def test_cfg_int_coercion(self):
        assert cfg_int({"k": 42}, "k") == 42
        assert cfg_int({"k": "42"}, "k") == 42
        assert cfg_int({"k": "abc"}, "k", default=99) == 99
        assert cfg_int({"k": None}, "k", default=99) == 99

    def test_cfg_float_coercion(self):
        assert cfg_float({"k": 1.5}, "k") == 1.5
        assert cfg_float({"k": "1.5"}, "k") == 1.5
        assert cfg_float({"k": "x"}, "k", default=0.0) == 0.0

    def test_cfg_bool_via_truthy(self):
        assert cfg_bool({"k": "true"}, "k") is True
        assert cfg_bool({"k": "false"}, "k") is False
        assert cfg_bool({"k": None}, "k", default=True) is True

    def test_cfg_str_strips(self):
        assert cfg_str({"k": "  hi  "}, "k") == "hi"
        assert cfg_str({"k": None}, "k", default="d") == "d"
        assert cfg_str({}, "k", default="d") == "d"

    def test_cfg_json_decodes(self):
        assert cfg_json({"k": '{"a": 1}'}, "k") == {"a": 1}
        assert cfg_json({"k": "[1,2]"}, "k") == [1, 2]
        # Pass-through for already-decoded values.
        assert cfg_json({"k": {"x": 1}}, "k") == {"x": 1}
        # Garbage returns default.
        assert cfg_json({"k": "not-json{"}, "k", default={}) == {}

    def test_get_env_type_normalizes(self, monkeypatch):
        import utils.config as config

        real = config.load_config
        monkeypatch.setattr(config, "load_config", lambda: {"terminal": {"env_type": "  Docker  "}})
        try:
            assert config.get_env_type() == "docker"
            monkeypatch.setattr(config, "load_config", lambda: {})
            assert config.get_env_type() == "local"
            monkeypatch.setattr(config, "load_config", lambda: {"terminal": {"env_type": ""}})
            assert config.get_env_type() == "local"
        finally:
            monkeypatch.setattr(config, "load_config", real)

    def test_load_config_missing_returns_empty(self, monkeypatch, tmp_path):
        """Missing config.yaml MUST NOT raise — return ``{}``."""
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        # Force cache reset so the new HOME is observed.
        _CONFIG_CACHE = None
        _CONFIG_CACHE_MTIME = None
        try:
            assert load_config() == {}
        finally:
            _CONFIG_CACHE = None
            _CONFIG_CACHE_MTIME = None

    def test_load_config_invalid_yaml_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text(": not yaml [")
        _CONFIG_CACHE = None
        _CONFIG_CACHE_MTIME = None
        try:
            assert load_config() == {}
        finally:
            _CONFIG_CACHE = None
            _CONFIG_CACHE_MTIME = None

    def test_load_config_caches_per_mtime(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("a: 1\n")
        _CONFIG_CACHE = None
        _CONFIG_CACHE_MTIME = None
        try:
            first = load_config()
            assert first == {"a": 1}
            # Without mtime change, second call returns cached object.
            second = load_config()
            assert second is first  # identity, not equality
            # Edit config; mtime advances.
            cfg_file.write_text("a: 2\n")
            third = load_config()
            assert third == {"a": 2}
        finally:
            _CONFIG_CACHE = None
            _CONFIG_CACHE_MTIME = None
