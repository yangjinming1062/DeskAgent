import json
import os
import tempfile

import pytest

from tools import discover_builtin_tools, registry


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
        tmp = os.path.join(tempfile.gettempdir(), "spiritagent_test_rw.txt")
        try:
            registry.dispatch("write_file", {"path": tmp, "content": "test123"})
            r = json.loads(registry.dispatch("read_file", {"path": tmp}))
            assert "test123" in str(r)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_search_files_mode(self, tmp_path):
        (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
        (tmp_path / "drop.md").write_text("x", encoding="utf-8")
        r = json.loads(
            registry.dispatch(
                "search_files",
                {"pattern": "*.txt", "target": "files", "path": str(tmp_path)},
            )
        )
        assert "error" not in r
        assert {f.replace("\\", "/") for f in r.get("files", [])} == {"keep.txt"}
