#!/usr/bin/env python3
"""Static import-shape checks for backend and runner.

Catches the regressions that shipped together in c66ab1a (and the original
forward-reference bug from the bug log):

  A. ``TYPE_CHECKING`` leak — a name imported under ``if TYPE_CHECKING:``
     is also referenced outside that block (e.g. a class-body annotation
     like ``dict[int, JsonRpcDispatcher]``). Type-checkers see it; the
     runtime sees ``NameError``.

  B. ``tools`` → ``core`` eager import — ``backend/tools/**/*.py`` doing
     ``from core import ...`` at module level triggers the
     ``core/__init__`` → ``chat_service`` → ``tools_registry`` → ``tools``
     circular import. The established convention is to lazy-import
     inside the function body.

  C. Facade consistency — for ``from <local_pkg> import X`` (absolute or
     single-segment relative import) where ``<local_pkg>`` resolves to
     a package in this repo, ``X`` must be re-exported by
     ``<local_pkg>/__init__.py``. Protects against trimming facades
     too aggressively (c66ab1a almost broke this; future trims could).

Exits non-zero with one diagnostic per violation if any file fails.
Files passed as argv are checked; with no argv, walks ``backend/`` and
``runner/`` (excluding virtualenvs and __pycache__).
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = [REPO_ROOT / "backend", REPO_ROOT / "runner"]


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


# ---- A. TYPE_CHECKING leak ----------------------------------------------------


def _tc_ranges(tree: ast.Module) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


def _in_any(ranges: list[tuple[int, int]], lineno: int) -> bool:
    return any(start <= lineno <= end for start, end in ranges)


def _names_imported_under_tc(tree: ast.Module, tc_ranges: list[tuple[int, int]]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if not _in_any(tc_ranges, node.lineno):
            continue
        for alias in node.names:
            out.add(alias.asname or alias.name.split(".")[0])
    return out


def _names_referenced_outside_tc(tree: ast.Module, tc_ranges: list[tuple[int, int]]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or _in_any(tc_ranges, lineno):
            continue
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                out.add(root.id)
    return out


def check_type_checking_leak(path: Path) -> list[str]:
    tree = _parse(path)
    if tree is None:
        return []
    tc_ranges = _tc_ranges(tree)
    if not tc_ranges:
        return []
    tc_imports = _names_imported_under_tc(tree, tc_ranges)
    if not tc_imports:
        return []
    used_outside = _names_referenced_outside_tc(tree, tc_ranges)
    leaks = sorted(tc_imports & used_outside)
    return [f"{path}: '{name}' imported under TYPE_CHECKING but used outside it " f"(annotation evaluated at runtime → NameError)" for name in leaks]


# ---- B. backend/tools → core eager import -------------------------------------


def check_tools_eager_core_import(path: Path) -> list[str]:
    """``backend/tools/**/*.py`` must not have module-level ``from core import ...``.

    The core<->tools cycle:
        core/__init__ -> chat_service -> tools_registry -> tools/__init__ -> <file> -> core (partial)
    The convention is to ``from core import ...`` *inside* the function that needs it.
    """
    try:
        rel = path.resolve().relative_to((REPO_ROOT / "backend").resolve())
    except ValueError:
        return []
    parts = rel.parts
    if not parts or parts[0] != "tools":
        return []
    if parts[-1] == "__init__.py":
        return []

    tree = _parse(path)
    if tree is None:
        return []
    errors: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 0 or node.module != "core":
            continue
        names = ", ".join(a.name for a in node.names)
        errors.append(f"{path}:{node.lineno}: module-level `from core import {names}` — " "breaks the core<->tools cycle, move inside the function body")
    return errors


# ---- B2. runner/tools/* cross-subpackage eager import -------------------------


def _runner_subpackages() -> set[str]:
    """Names of subpackages under ``runner/tools/`` — sibling dirs that contain
    their own ``__init__.py``. Direct children that are *modules* (e.g. ``registry``,
    ``interrupt``, ``security``) are excluded: those are stable, top-level shared
    utilities and don't form cycles.
    """
    root = REPO_ROOT / "runner" / "tools"
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}


def check_runner_subpkg_eager_import(path: Path) -> list[str]:
    """``runner/tools/<subpkg>/<file>.py`` must not module-level import a *sibling
    subpackage* (``from ..files import ...``, ``from ..multimodal import ...``).

    Same intent as ``check_tools_eager_core_import``: surface the eager cross-
    subpackage imports that have caused real circular-import regressions
    (terminal_tool <-> file_tools, code_execution_tool -> thread_context ->
    terminal_tool -> file_tools). The established convention is to lazy-import
    inside the function body. Imports from sibling *modules* (``from ..registry``,
    ``from ..interrupt``, ``from ..security``) are allowed — they are stable
    shared utilities that don't form cycles.
    """
    try:
        rel = path.resolve().relative_to((REPO_ROOT / "runner" / "tools").resolve())
    except ValueError:
        return []
    parts = rel.parts
    if len(parts) < 2:
        return []
    if parts[-1] == "__init__.py":
        return []
    own_subpkg = parts[0]
    siblings = _runner_subpackages() - {own_subpkg}

    tree = _parse(path)
    if tree is None:
        return []
    errors: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level < 2 or node.module is None:
            continue
        if "." in node.module:
            continue
        if node.module not in siblings:
            continue
        names = ", ".join(a.name for a in node.names)
        errors.append(
            f"{path}:{node.lineno}: module-level `from ..{node.module} import {names}` "
            "crosses runner/tools subpackage boundaries - "
            "move inside the function body to avoid import cycles"
        )
    return errors


# ---- C. Facade consistency ----------------------------------------------------


def _facade_exports(init_path: Path) -> set[str] | None:
    """Re-exported names from a facade's ``__init__.py``.

    Returns ``None`` if the file is a stub (no re-exports) — caller should
    skip the facade in that case. Otherwise returns the union of:

      * names in ``__all__`` (if defined)
      * names imported by module-level ``from .X import Y`` statements
    """
    tree = _parse(init_path)
    if tree is None:
        return None

    names: set[str] = set()
    has_module_level_imports = False

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                names.add(elt.value)
                    break
        elif isinstance(node, ast.ImportFrom) and node.level >= 1:
            has_module_level_imports = True
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])

    if not has_module_level_imports and not names:
        return None
    return names


def _importer_scan_root(path: Path) -> Path | None:
    """Which scan root (backend/ or runner/) does this file live under?"""
    resolved = path.resolve()
    for root in SCAN_ROOTS:
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _resolve_facade_init(importer: Path, level: int, module: str | None) -> Path | None:
    """Resolve an ``ast.ImportFrom`` to a facade ``__init__.py`` path.

    Handles:
      * ``from X import Y`` — absolute; ``X`` is resolved under the
        importer's scan root (backend/ or runner/).
      * ``from .X import Y`` / ``from ..X import Y`` — single-segment
        relative; resolved relative to the importer's parent chain.
      * multi-segment modules (``from utils.constants import X``,
        ``from ..multimodal.helpers import X``) are skipped — these point
        at regular modules, not facades we can statically introspect.
    """
    if level == 0:
        if not module or "." in module:
            return None
        scan_root = _importer_scan_root(importer)
        if scan_root is None:
            return None
        candidate = (scan_root / module / "__init__.py").resolve()
        return candidate if candidate.is_file() else None

    # Relative: level >= 1
    if module and "." in module:
        return None
    parent_parts = importer.parent.resolve().parts
    up = level - 1
    if len(parent_parts) < up:
        return None
    base = Path(*parent_parts[: len(parent_parts) - up]) if up else Path(*parent_parts)
    target = base if module is None else base / module
    candidate = (target / "__init__.py").resolve()
    return candidate if candidate.is_file() else None


def check_facade_consistency(path: Path) -> list[str]:
    """For every ``from <local_pkg> import X`` that resolves to a facade
    with declared re-exports, ``X`` must be in those re-exports.

    Skips:
      * the facade's own ``__init__.py`` (it defines the exports)
      * imports whose target has no ``__init__.py`` (regular modules)
      * facades without any re-exports (``None`` from ``_facade_exports``)
    """
    if path.name == "__init__.py":
        return []

    tree = _parse(path)
    if tree is None:
        return []

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        init_path = _resolve_facade_init(path, node.level, node.module)
        if init_path is None:
            continue
        exports = _facade_exports(init_path)
        if exports is None:
            continue

        for alias in node.names:
            if alias.name == "*":
                continue
            if alias.name not in exports:
                where = f"absolute import '{node.module}'" if node.level == 0 else f"relative import (level={node.level}, module='{node.module}')"
                rel = init_path.relative_to(REPO_ROOT).parent.as_posix()
                errors.append(
                    f"{path}:{node.lineno}: '{alias.name}' imported via {where} "
                    f"is not re-exported by {rel}/__init__ — "
                    "either add the re-export or import from the underlying module"
                )
    return errors


# ---- main --------------------------------------------------------------------


def main(argv: list[str]) -> int:
    strict = False
    args: list[str] = []
    for a in argv:
        if a == "--strict-imports":
            strict = True
        else:
            args.append(a)

    if args:
        targets = [Path(a).resolve() for a in args]
    else:
        targets = []
        for root in SCAN_ROOTS:
            if not root.is_dir():
                continue
            targets.extend(p for p in root.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts)

    diagnostics: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        diagnostics.extend(check_type_checking_leak(path))
        diagnostics.extend(check_tools_eager_core_import(path))
        diagnostics.extend(check_runner_subpkg_eager_import(path))
        diagnostics.extend(check_facade_consistency(path))

    if diagnostics:
        for d in diagnostics:
            print(d, file=sys.stderr)
        suffix = " (strict)" if strict else " (advisory)"
        print(f"\n{len(diagnostics)} import-shape issue(s){suffix}", file=sys.stderr)
        return 1 if strict else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
