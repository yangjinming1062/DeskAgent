import json
import os
import tempfile

import pytest

from tools import discover_builtin_tools, registry, tool_error, tool_result


@pytest.fixture(scope="session", autouse=True)
def _discover_tools():
    discover_builtin_tools()


class TestRegistry:
    def test_tools_registered(self):
        assert len(registry.get_all_tool_names()) >= 15

    def test_get_schemas(self):
        schemas = registry.get_schemas()
        assert len(schemas) >= 15
        for s in schemas:
            assert "name" in s and "parameters" in s

    def test_tool_result(self):
        assert "foo" in json.loads(tool_result(foo="bar"))

    def test_tool_error(self):
        assert "error" in json.loads(tool_error("fail"))

    def test_dispatch_nonexistent(self):
        assert "error" in json.loads(registry.dispatch("nonexistent_xyz", {}))


class TestTerminal:
    def test_echo(self):
        r = json.loads(
            registry.dispatch("terminal", {"command": "echo hello", "force": True})
        )
        assert "hello" in str(r)


class TestFileTools:
    def test_write_and_read(self):
        tmp = os.path.join(tempfile.gettempdir(), "deskagent_test_rw.txt")
        try:
            registry.dispatch("write_file", {"path": tmp, "content": "test123"})
            r = json.loads(registry.dispatch("read_file", {"path": tmp}))
            assert "test123" in str(r)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_search(self):
        r = json.loads(
            registry.dispatch("search_files", {"pattern": "*.py", "target": "files"})
        )
        assert isinstance(r, dict)


class TestRedaction:
    """Boundary cases for ``utils.redact.redact_sensitive_text``.

    The previous ``(?<![A-Za-z0-9_-])`` / ``(?![A-Za-z0-9_-])`` anchors
    silently dropped any secret that sat at the start of the string, at
    the end of the string, or after ``=`` / ``"`` / space where there
    was no character on the boundary side. These tests pin the fix.
    """

    def test_secret_at_start_of_string(self):
        from utils.redact import redact_sensitive_text

        s = "sk-ant-api03-abcdefghijklmnop1234567890"
        out = redact_sensitive_text(s)
        # ``_mask_token`` keeps the 6-char prefix as a fingerprint, so
        # ``sk-ant`` remains — what we verify is that the secret PAYLOAD
        # is gone (i.e. the body is masked and the string is shorter).
        assert out != s
        assert "abcdefghijklmnop1234567890" not in out

    def test_secret_at_end_of_normal_line(self):
        from utils.redact import redact_sensitive_text

        s = "export MY_KEY=sk-ant-api03-abcdefghijklmnop1234567890"
        out = redact_sensitive_text(s)
        assert "abcdefghijklmnop1234567890" not in out
        assert "MY_KEY" in out

    def test_secret_in_json_value(self):
        from utils.redact import redact_sensitive_text

        s = '{"api_key": "sk-ant-api03-abcdefghijklmnop1234567890"}'
        out = redact_sensitive_text(s)
        assert "sk-ant" not in out
        assert '"api_key"' in out

    def test_bearer_token_at_end_of_line(self):
        from utils.redact import redact_sensitive_text

        s = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234"
        out = redact_sensitive_text(s)
        assert "abcdefghijklmnop" not in out
        assert "***" in out

    def test_secret_inside_longer_token_still_skipped(self):
        # The word-boundary anchor means a substring of a longer
        # alphanumeric word is intentionally NOT redacted (it would
        # over-match in base64 blobs). This pins that behavior.
        from utils.redact import redact_sensitive_text

        s = "Xsk-ant-api03-abcdefghijklmnop1234567890"
        out = redact_sensitive_text(s)
        assert out == s
