from utils import get_disabled_config_names


def get_disabled_toolset_ids() -> set[str]:
    """Read the ``toolsets.disabled`` list from the in-memory config.

    Symmetric with ``tools/skills/helpers.py::get_disabled_skill_names``.
    Stored in a sibling ``toolsets`` YAML section so the atomic-write lock
    in ``config-writer.cjs`` keeps skills and toolsets writes serialized.
    """
    return get_disabled_config_names(section="toolsets")
