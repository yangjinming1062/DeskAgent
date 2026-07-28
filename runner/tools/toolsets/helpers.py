from tools.skills import get_disabled_skill_names


def get_disabled_toolset_ids() -> set[str]:
    """Read the ``toolsets.disabled`` list from ``~/.deskagent/config.yaml``.

    Symmetric with ``tools/skills/helpers.py::get_disabled_skill_names``.
    Stored in a sibling ``toolsets`` YAML section so the atomic-write lock
    in ``config-writer.cjs`` keeps skills and toolsets writes serialized.
    """
    return get_disabled_skill_names(section="toolsets")


__all__ = ["get_disabled_toolset_ids"]
