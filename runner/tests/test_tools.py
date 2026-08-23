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

    def test_import_failure_classification(self, monkeypatch):
        import importlib

        from tools import discover_builtin_tools_strict

        orig_import = importlib.import_module

        def _mock_import(name):
            if name == "tools.broken_optional":
                raise ModuleNotFoundError("No module named 'broken_optional'")
            if name == "tools.broken_fatal":
                raise RuntimeError("Syntax error in broken_fatal")
            return orig_import(name)

        monkeypatch.setattr(importlib, "import_module", _mock_import)
        monkeypatch.setattr(
            "pkgutil.walk_packages",
            lambda _path=None, _prefix=None: [(None, "tools.broken_optional", False), (None, "tools.broken_fatal", False)],
        )

        imported, failures = discover_builtin_tools_strict()
        assert "tools.broken_optional" not in failures
        assert "tools.broken_fatal" in failures
        assert "RuntimeError" in failures["tools.broken_fatal"]


class TestTerminal:
    def test_echo(self):
        r = json.loads(
            registry.dispatch("terminal", {"command": "echo hello", "force": True}),
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
            ),
        )
        assert "error" not in r
        assert {f.replace("\\", "/") for f in r.get("files", [])} == {"keep.txt"}
