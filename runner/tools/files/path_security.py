from pathlib import Path


def validate_within_dir(path: Path, root: Path) -> str | None:
    """Returns error if path resolves outside root, None if safe."""
    try:
        path.resolve().relative_to(root.resolve())
        return None
    except (ValueError, OSError) as e:
        return f"Path escapes allowed directory: {e}"


def has_traversal_component(path_str: str) -> bool:
    return ".." in Path(path_str).parts
