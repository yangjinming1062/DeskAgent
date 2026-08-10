import re
from pathlib import Path
from typing import Any

import yaml
from utils import get_disabled_config_names

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split ``content`` into ``(frontmatter, body)``.

    Returns an empty dict and the original body if no frontmatter block
    is present. Frontmatter is parsed as YAML; YAML parse errors are
    swallowed (empty dict) to keep skill_view resilient to malformed
    manifests.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}, content[match.end() :]
    return data if isinstance(data, dict) else {}, content[match.end() :]


def iter_skill_index_files(directory: Path | str, name: str = "SKILL.md"):
    """Yield every ``name`` file under ``directory`` (recursive)."""
    root = Path(directory)
    if not root.is_dir():
        return
    yield from sorted(root.rglob(name))


def get_deskagent_metadata(frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """Return ``frontmatter.metadata.deskagent`` as a dict, or ``{}`` if any link is missing/wrong type."""
    if not isinstance(frontmatter, dict):
        return {}
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    deskagent = metadata.get("deskagent")
    return deskagent if isinstance(deskagent, dict) else {}


def get_disabled_skill_names(section: str = "skills") -> set[str]:
    """Read the ``<section>.disabled`` list from ``~/.deskagent/config.yaml``.

    ``section`` defaults to ``"skills"``; pass ``"toolsets"`` to share the
    same parse path for the sibling toolsets section.
    """
    return get_disabled_config_names(section)
