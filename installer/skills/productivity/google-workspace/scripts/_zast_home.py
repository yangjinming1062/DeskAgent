"""Resolve ZAST_HOME for standalone skill scripts.

Skill scripts may run outside the Zast process (e.g. system Python,
nix env, CI) where ``runtime.constants`` is not importable.  This module
provides the same ``get_zast_home()`` and ``display_zast_home()``
contracts as ``runtime.constants`` without requiring it on ``sys.path``.

When ``runtime.constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``ZAST_HOME = Path(os.getenv(...))`` pattern.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from runtime.constants import display_zast_home as display_zast_home
    from runtime.constants import get_zast_home as get_zast_home
except (ModuleNotFoundError, ImportError):

    def get_zast_home() -> Path:
        """Return the Zast home directory (default: ~/.zast).

        Mirrors ``constants.get_zast_home()``."""
        val = os.environ.get("ZAST_HOME", "").strip()
        return Path(val) if val else Path.home() / ".zast"

    def display_zast_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``constants.display_zast_home()``."""
        home = get_zast_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
