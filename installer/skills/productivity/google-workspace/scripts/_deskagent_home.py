"""Resolve DESKAGENT_HOME for standalone skill scripts.

Skill scripts may run outside the DeskAgent process (e.g. system Python,
nix env, CI) where ``runtime.constants`` is not importable.  This module
provides the same ``get_deskagent_home()`` and ``display_deskagent_home()``
contracts as ``runtime.constants`` without requiring it on ``sys.path``.

When ``runtime.constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``DESKAGENT_HOME = Path(os.getenv(...))`` pattern.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from runtime.constants import display_deskagent_home as display_deskagent_home
    from runtime.constants import get_deskagent_home as get_deskagent_home
except (ModuleNotFoundError, ImportError):

    def get_deskagent_home() -> Path:
        """Return the DeskAgent home directory (default: ~/.deskagent).

        Mirrors ``constants.get_deskagent_home()``."""
        val = os.environ.get("DESKAGENT_HOME", "").strip()
        return Path(val) if val else Path.home() / ".deskagent"

    def display_deskagent_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``constants.display_deskagent_home()``."""
        home = get_deskagent_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
