import functools
import json
import logging
import shutil
import sys
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils import atomic_replace, get_skills_dir

from .helpers import get_deskagent_metadata, iter_skill_index_files, parse_frontmatter

# ``fcntl`` (POSIX) and ``msvcrt`` (Windows) are stdlib — they are not and
# must not be listed in pyproject.toml (that file is for third-party deps).
# The stdlib ships one of these on every supported platform; the platform
# is decided once at interpreter startup and cannot change mid-process.
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_VALID_STATES = {STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED}


@functools.cache
def _load_protected_builtins() -> frozenset[str]:
    """Skill names declaring ``metadata.deskagent.protected: true`` in their SKILL.md.

    Per-skill OSError and YAML errors are swallowed so a single malformed
    manifest doesn't disable curator protection across the board.
    Call ``_load_protected_builtins.cache_clear()`` after installing new skills.
    """
    protected: set[str] = set()
    for skill_md in iter_skill_index_files(get_skills_dir()):
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, _ = parse_frontmatter(content)
        if not get_deskagent_metadata(frontmatter).get("protected"):
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


def _archive_dir() -> Path:
    return get_skills_dir() / ".archive"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def latest_activity_at(record: dict[str, Any]) -> str | None:
    dates = [(dt, str(raw)) for key in ("last_used_at", "last_viewed_at", "last_patched_at") if (raw := record.get(key)) and (dt := _parse_iso_timestamp(raw))]
    return max(dates, key=lambda x: x[0])[1] if dates else None


def activity_count(record: dict[str, Any]) -> int:
    return sum(int(record.get(k) or 0) for k in ("use_count", "view_count", "patch_count"))


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


def list_agent_created_skill_names() -> list[str]:
    base = get_skills_dir()
    if not base.exists():
        return []
    hub, usage = _read_hub_installed_names(), load_usage()
    names = []
    for skill_md in base.rglob("SKILL.md"):
        if not is_excluded_skill_path(skill_md):
            try:
                skill_md.relative_to(base)
                name = _read_skill_name(skill_md, fallback=skill_md.parent.name)
                if name not in hub and not is_protected_builtin(name) and _is_curator_managed_record(usage.get(name)):
                    names.append(name)
            except ValueError:
                pass
    return sorted(set(names))


def list_archived_skill_names() -> list[str]:
    archive_root = _archive_dir()
    return sorted({p.name for p in archive_root.iterdir() if p.is_dir()}) if archive_root.exists() else []


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


def is_agent_created(skill_name: str) -> bool:
    return not is_hub_installed(skill_name)


def is_hub_installed(skill_name: str) -> bool:
    return skill_name in _read_hub_installed_names()


def is_curation_eligible(skill_name: str) -> bool:
    return not is_protected_builtin(skill_name) and not is_hub_installed(skill_name)


def _is_curator_managed_record(record: Any) -> bool:
    return isinstance(record, dict) and (record.get("created_by") == "agent" or record.get("agent_created") is True)


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


def seed_record_if_missing(skill_name: str) -> None:
    if skill_name and is_curation_eligible(skill_name):
        try:
            with _usage_file_lock():
                if not isinstance((data := load_usage()).get(skill_name), dict):
                    data[skill_name] = _empty_record()
                    save_usage(data)
        except Exception:
            pass


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


def set_state(skill_name: str, state: str) -> None:
    if state in _VALID_STATES:
        _mutate(skill_name, lambda r: r.update({"state": state, "archived_at": _now_iso() if state == STATE_ARCHIVED else None}), require_curation_eligible=True)


def set_pinned(skill_name: str, pinned: bool) -> None:
    _mutate(skill_name, lambda r: r.update({"pinned": bool(pinned)}), require_curation_eligible=True)


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


def archive_skill(skill_name: str) -> tuple[bool, str]:
    if not is_curation_eligible(skill_name):
        if is_protected_builtin(skill_name):
            return (False, f"skill '{skill_name}' is a protected built-in; it backs load-bearing UX and is never archived or consolidated")
        return False, f"skill '{skill_name}' is hub-installed; never archive"

    if (skill_dir := _find_skill_dir(skill_name)) is None:
        return False, f"skill '{skill_name}' not found"

    archive_root = _archive_dir()
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
        dest = archive_root / skill_dir.name
        if dest.exists():
            dest = archive_root / f"{skill_dir.name}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        try:
            skill_dir.rename(dest)
        except OSError:
            shutil.move(str(skill_dir), str(dest))
        set_state(skill_name, STATE_ARCHIVED)
        return True, f"archived to {dest}"
    except Exception as e:
        return False, f"failed to archive: {e}"


def restore_skill(skill_name: str) -> tuple[bool, str]:
    if is_hub_installed(skill_name):
        return False, f"skill '{skill_name}' is hub-installed; restore would shadow the upstream version"
    archive_root = _archive_dir()
    if not archive_root.exists():
        return False, "no archive directory"

    candidates = [p for p in archive_root.rglob("*") if p.is_dir() and p.name == skill_name] or sorted(
        [p for p in archive_root.rglob("*") if p.is_dir() and p.name.startswith(f"{skill_name}-")], key=lambda p: p.name, reverse=True
    )
    if not candidates:
        return False, f"skill '{skill_name}' not found in archive"

    src, dest = candidates[0], get_skills_dir() / skill_name
    if dest.exists():
        return False, f"destination already exists: {dest}"

    try:
        try:
            src.rename(dest)
        except OSError:
            shutil.move(str(src), str(dest))
        set_state(skill_name, STATE_ACTIVE)
        return True, f"restored to {dest}"
    except Exception as e:
        return False, f"failed to restore: {e}"


def _find_skill_dir(skill_name: str) -> Path | None:
    base = get_skills_dir()
    if not base.exists():
        return None
    for skill_md in base.rglob("SKILL.md"):
        if is_excluded_skill_path(skill_md):
            continue
        if _read_skill_name(skill_md, fallback=skill_md.parent.name) == skill_name:
            return skill_md.parent
    return None


def provenance(skill_name: str) -> str:
    return "hub" if is_hub_installed(skill_name) else "agent"


def usage_report() -> list[dict[str, Any]]:
    base = get_skills_dir()
    if not base.exists():
        return []
    data, rows, seen = load_usage(), [], set()
    for skill_md in base.rglob("SKILL.md"):
        if is_excluded_skill_path(skill_md):
            continue
        name = _read_skill_name(skill_md, fallback=skill_md.parent.name)
        if name in seen:
            continue
        seen.add(name)
        rec = _empty_record() | (data.get(name) if isinstance(data.get(name), dict) else {})
        row = {"name": name, **rec, "provenance": provenance(name), "_persisted": isinstance(data.get(name), dict)}
        row["last_activity_at"] = latest_activity_at(row)
        row["activity_count"] = activity_count(row)
        rows.append(row)
    return sorted(rows, key=lambda r: r["name"])
