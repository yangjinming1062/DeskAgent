import functools
import json
import logging
import sys
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils import atomic_replace, get_skills_dir

from .helpers import get_spiritagent_metadata, iter_skill_index_files, parse_frontmatter

# fcntl（POSIX）与 msvcrt（Windows）属于标准库 — 不应也不允许列在 pyproject.toml（那里仅放第三方依赖）。
# 标准库在每个支持的平台上都会附带其中之一；平台在解释器启动时确定，进程中途不会改变。
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

STATE_ACTIVE = "active"


@functools.cache
def _load_protected_builtins() -> frozenset[str]:
    """在 SKILL.md 的 metadata.spiritagent.protected: true 上声明的 skill 名集合。

    单个 skill 的 OSError / YAML 错误会被吞掉，避免一个畸形 manifest 整体禁用 curator 保护。
    安装新 skill 后请调用 _load_protected_builtins.cache_clear()。
    """
    protected: set[str] = set()
    for skill_md in iter_skill_index_files(get_skills_dir()):
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, _ = parse_frontmatter(content)
        if not get_spiritagent_metadata(frontmatter).get("protected"):
            continue
        name = frontmatter.get("name")
        if isinstance(name, str) and name:
            protected.add(name)
    return frozenset(protected)


def is_protected_builtin(skill_name: str) -> bool:
    return skill_name in _load_protected_builtins()


def _usage_file() -> Path:
    return get_skills_dir() / ".usage.json"


def is_excluded_skill_path(path: Path) -> bool:
    return any(p.startswith(".") or p in {"__pycache__", "venv", ".venv", "node_modules"} for p in path.parts)


@contextmanager
def _usage_file_lock() -> None:
    lock_path = _usage_file().with_suffix(".json.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as fd:
        try:
            if sys.platform == "win32":
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if sys.platform == "win32":
                with suppress(OSError):
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                with suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_hub_installed_names() -> set[str]:
    lock = get_skills_dir() / ".hub" / "lock.json"
    if not lock.exists():
        return set()
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        installed = data.get("installed", {}) if isinstance(data, dict) else {}
        names = {str(k) for k in installed}
        sdir = get_skills_dir()
        for entry in installed.values():
            if isinstance(entry, dict) and (ip := entry.get("install_path")):
                try:
                    res = Path(ip).resolve() if Path(ip).is_absolute() else (sdir / ip).resolve()
                    res.relative_to(sdir.resolve())
                    if (s_md := res / "SKILL.md").exists():
                        names.add(_read_skill_name(s_md, fallback=res.name))
                except (OSError, ValueError):
                    pass
        return names
    except (OSError, json.JSONDecodeError):
        return set()


def _read_skill_name(skill_md: Path, fallback: str) -> str:
    try:
        text, in_front = (skill_md.read_text(encoding="utf-8", errors="replace")[:4000], False)
        for line in text.splitlines():
            if line.strip() == "---":
                if in_front:
                    break
                in_front = True
            elif in_front and line.strip().startswith("name:"):
                if val := line.strip().split(":", 1)[1].strip().strip("\"'"):
                    return val
    except OSError:
        pass
    return fallback


def is_hub_installed(skill_name: str) -> bool:
    return skill_name in _read_hub_installed_names()


def is_curation_eligible(skill_name: str) -> bool:
    return not is_protected_builtin(skill_name) and not is_hub_installed(skill_name)


def _empty_record() -> dict[str, Any]:
    return {
        "created_by": None,
        "use_count": 0,
        "view_count": 0,
        "last_used_at": None,
        "last_viewed_at": None,
        "patch_count": 0,
        "last_patched_at": None,
        "created_at": _now_iso(),
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
    }


def load_usage() -> dict[str, dict[str, Any]]:
    path = _usage_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_usage(data: dict[str, dict[str, Any]]) -> None:
    try:
        atomic_replace(str(_usage_file()), json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    except Exception as e:
        logger.debug("Failed to write %s: %s", _usage_file(), e, exc_info=True)


def get_record(skill_name: str) -> dict[str, Any]:
    rec = load_usage().get(skill_name)
    return _empty_record() | (rec if isinstance(rec, dict) else {})


def _mutate(skill_name: str, mutator: Callable[[dict[str, Any]], Any], *, require_curation_eligible: bool = False) -> None:
    if skill_name and not (require_curation_eligible and not is_curation_eligible(skill_name)):
        try:
            with _usage_file_lock():
                data = load_usage()
                rec = data.get(skill_name) if isinstance(data.get(skill_name), dict) else _empty_record()
                mutator(rec)
                data[skill_name] = rec
                save_usage(data)
        except Exception:
            pass


def bump_view(skill_name: str) -> None:
    _mutate(skill_name, lambda r: r.update({"view_count": int(r.get("view_count") or 0) + 1, "last_viewed_at": _now_iso()}))


def bump_use(skill_name: str) -> None:
    _mutate(skill_name, lambda r: r.update({"use_count": int(r.get("use_count") or 0) + 1, "last_used_at": _now_iso()}))


def bump_patch(skill_name: str) -> None:
    _mutate(skill_name, lambda r: r.update({"patch_count": int(r.get("patch_count") or 0) + 1, "last_patched_at": _now_iso()}))


def mark_agent_created(skill_name: str) -> None:
    _mutate(skill_name, lambda r: r.update({"created_by": "agent"}), require_curation_eligible=True)


def forget(skill_name: str) -> None:
    if skill_name:
        try:
            with _usage_file_lock():
                data = load_usage()
                if skill_name in data:
                    del data[skill_name]
                    save_usage(data)
        except Exception:
            pass
