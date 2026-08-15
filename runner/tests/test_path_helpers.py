import os
import platform
from unittest import mock

import pytest

from utils.path_helpers import SANE_PATH, append_sane_path_entries, find_python


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the lru_cache before each test."""
    find_python.cache_clear()
    yield


def test_find_python_prefers_env_override(tmp_path):
    """DESKAGENT_PYTHON env var takes highest priority."""
    stub = tmp_path / "custom-python"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    with mock.patch.dict(os.environ, {"DESKAGENT_PYTHON": str(stub)}, clear=False):
        assert find_python() == str(stub)


def test_find_python_returns_deskagent_home_venv(tmp_path):
    """When DESKAGENT_HOME is set and the venv python exists, return it."""
    is_win = platform.system() == "Windows"
    if is_win:
        venv_py = tmp_path / "runner" / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = tmp_path / "runner" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True, exist_ok=True)
    venv_py.write_text("#!/bin/sh\nexit 0\n")
    venv_py.chmod(0o755)
    with mock.patch.dict(os.environ, {"DESKAGENT_HOME": str(tmp_path)}, clear=False):
        os.environ.pop("DESKAGENT_PYTHON", None)
        result = find_python()
    assert result == str(venv_py)


def test_find_python_returns_none_when_nothing():
    """When no env, no venv, and no uv on PATH, return None."""
    env = {
        "DESKAGENT_PYTHON": "",
        "DESKAGENT_HOME": "/nonexistent_deskagent_home_test_only",
        "HOME": "/nonexistent_home_test_only",
        "LOCALAPPDATA": "/nonexistent_lap_test_only",
    }
    # Patch shutil.which to prevent finding a real uv binary on the host.
    with mock.patch.dict(os.environ, env, clear=False):
        with mock.patch("utils.path_helpers.shutil.which", return_value=None):
            result = find_python()
    assert result is None


def test_append_sane_path_entries_posix_merges_deduped():
    """POSIX branch: existing entries keep their order, SANE_PATH fills gaps, no dupes."""
    with mock.patch("utils.path_helpers.IS_WINDOWS", False):
        merged = append_sane_path_entries("/usr/local/bin:/usr/bin:/extra")
    assert merged.startswith("/usr/local/bin:/usr/bin:/extra:")
    assert "/opt/homebrew/bin" in merged
    assert merged.count("/usr/bin") == 1
    assert merged == ":".join(dict.fromkeys(merged.split(":")))


def test_append_sane_path_entries_posix_empty_path():
    with mock.patch("utils.path_helpers.IS_WINDOWS", False):
        assert append_sane_path_entries("") == SANE_PATH
