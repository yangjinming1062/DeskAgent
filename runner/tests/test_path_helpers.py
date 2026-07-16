import os
import platform
from pathlib import Path
from unittest import mock

import pytest
from utils.path_helpers import find_python


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the lru_cache before each test."""
    find_python.cache_clear()
    yield


def test_find_python_prefers_env_override(tmp_path):
    """ZAST_PYTHON env var takes highest priority."""
    stub = tmp_path / "custom-python"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    with mock.patch.dict(os.environ, {"ZAST_PYTHON": str(stub)}, clear=False):
        assert find_python() == str(stub)


def test_find_python_returns_zast_home_venv(tmp_path):
    """When ZAST_HOME is set and the venv python exists, return it."""
    is_win = platform.system() == "Windows"
    if is_win:
        venv_py = tmp_path / "runner" / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = tmp_path / "runner" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True, exist_ok=True)
    venv_py.write_text("#!/bin/sh\nexit 0\n")
    venv_py.chmod(0o755)
    with mock.patch.dict(os.environ, {"ZAST_HOME": str(tmp_path)}, clear=False):
        os.environ.pop("ZAST_PYTHON", None)
        result = find_python()
    assert result == str(venv_py)


def test_find_python_returns_none_when_nothing():
    """When no env, no venv, and no uv on PATH, return None."""
    env = {
        "ZAST_PYTHON": "",
        "ZAST_HOME": "/nonexistent_zast_home_test_only",
        "HOME": "/nonexistent_home_test_only",
        "LOCALAPPDATA": "/nonexistent_lap_test_only",
    }
    # Patch shutil.which to prevent finding a real uv binary on the host.
    with mock.patch.dict(os.environ, env, clear=False):
        with mock.patch("utils.path_helpers.shutil.which", return_value=None):
            result = find_python()
    assert result is None
