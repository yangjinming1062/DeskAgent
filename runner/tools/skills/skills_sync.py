#!/usr/bin/env python3
import contextlib
import hashlib
import logging
import os
import shutil
import stat
from pathlib import Path, PurePosixPath

from utils import atomic_replace, get_deskagent_home, get_skills_dir

from .skill_usage import _load_protected_builtins, is_excluded_skill_path, read_suppressed_names
from .skills_guard import content_hash

logger = logging.getLogger(__name__)

DESKAGENT_HOME = get_deskagent_home()
# Backwards-compat module-level constants: callers that imported these
# before the lazy-resolution refactor still see Paths. New code inside
# this module MUST call ``_skills_dir()`` / ``_deskagent_home()`` so a
# test (or a profile switch) that changes ``DESKAGENT_HOME`` between
# calls gets fresh paths.
SKILLS_DIR = get_skills_dir()
MANIFEST_FILE = SKILLS_DIR / ".bundled_manifest"
NO_BUNDLED_SKILLS_MARKER = ".no-bundled-skills"


def _deskagent_home() -> Path:
    return get_deskagent_home()


def _skills_dir() -> Path:
    return get_skills_dir()


def _read_manifest() -> dict[str, str]:
    manifest_file = _skills_dir() / ".bundled_manifest"
    try:
        return (
            {(parts := line.partition(":"))[0].strip(): parts[2].strip() for line in manifest_file.read_text(encoding="utf-8").splitlines() if line.strip()}
            if manifest_file.exists()
            else {}
        )
    except OSError:
        return {}


def _read_suppressed_names() -> set[str]:
    try:
        return read_suppressed_names()
    except Exception:
        path = _skills_dir() / ".curator_suppressed"
        try:
            return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")} if path.exists() else set()
        except OSError:
            return set()


def _write_manifest(entries: dict[str, str]) -> None:
    data = "".join(f"{name}:{hash_val}\n" for name, hash_val in sorted(entries.items()))
    manifest_file = _skills_dir() / ".bundled_manifest"
    try:
        atomic_replace(str(manifest_file), data)
    except Exception as e:
        logger.debug("Failed to write skills manifest %s: %s", manifest_file, e, exc_info=True)


def _read_skill_name(skill_md: Path, fallback: str) -> str:
    try:
        content, in_frontmatter = skill_md.read_text(encoding="utf-8", errors="replace")[:4000], False
        for line in content.splitlines():
            if (stripped := line.strip()) == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
            elif in_frontmatter and stripped.startswith("name:"):
                if val := stripped.split(":", 1)[1].strip().strip("\"'"):
                    return val
    except OSError:
        pass
    return fallback


def _discover_bundled_skills(bundled_dir: Path) -> list[tuple[str, Path]]:
    return [(_read_skill_name(sm, sm.parent.name), sm.parent) for sm in bundled_dir.rglob("SKILL.md") if not is_excluded_skill_path(sm)] if bundled_dir.exists() else []


def _compute_relative_dest(skill_dir: Path, bundled_dir: Path) -> Path:
    return _skills_dir() / skill_dir.relative_to(bundled_dir)


def _dir_hash(directory: Path) -> str:
    hasher = hashlib.md5()
    try:
        for fpath in sorted(directory.rglob("*")):
            if fpath.is_file():
                hasher.update(str(fpath.relative_to(directory)).encode("utf-8"))
                hasher.update(fpath.read_bytes())
    except OSError:
        pass
    return hasher.hexdigest()


def _safe_rel_install_path(path: Path, base: Path) -> str:
    rel = path.relative_to(base)
    posix = rel.as_posix()
    parts = [part for part in PurePosixPath(posix).parts if part not in {"", "."}]
    if PurePosixPath(posix).is_absolute() or not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe optional skill path: {posix}")
    return "/".join(parts)


def _skill_file_list(skill_dir: Path) -> list[str]:
    return [fpath.relative_to(skill_dir).as_posix() for fpath in sorted(skill_dir.rglob("*")) if fpath.is_file()]


def _content_hash(directory: Path) -> str:
    try:
        return content_hash(directory)
    except Exception:
        return _dir_hash(directory)


def sync_skills(quiet: bool = False) -> dict:
    deskagent_home = _deskagent_home()
    skills_dir = _skills_dir()
    if (deskagent_home / NO_BUNDLED_SKILLS_MARKER).exists():
        if not quiet:
            logger.info("skipped: profile opted out of bundled skills via .no-bundled-skills")
        return {"copied": [], "updated": [], "skipped": 0, "user_modified": [], "cleaned": [], "total_bundled": 0, "skipped_opt_out": True}

    bundled_dir = get_skills_dir()
    if not bundled_dir.exists():
        return {"copied": [], "updated": [], "skipped": 0, "user_modified": [], "cleaned": [], "suppressed": [], "total_bundled": 0}

    skills_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()
    bundled_skills = _discover_bundled_skills(bundled_dir)
    bundled_names = {name for name, _ in bundled_skills}
    suppressed = _read_suppressed_names()

    copied, updated, user_modified, suppressed_skipped, skipped = [], [], [], [], 0

    for skill_name, skill_src in bundled_skills:
        if skill_name in suppressed:
            suppressed_skipped.append(skill_name)
            continue

        dest = _compute_relative_dest(skill_src, bundled_dir)
        bundled_hash = _dir_hash(skill_src)

        if skill_name not in manifest:
            try:
                if dest.exists():
                    skipped += 1
                    if _dir_hash(dest) == bundled_hash:
                        manifest[skill_name] = bundled_hash
                    elif not quiet:
                        logger.warning("bundled version of %s skipped: user has local skill (run `deskagent skills reset %s`)", skill_name, skill_name)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_src, dest)
                    copied.append(skill_name)
                    manifest[skill_name] = bundled_hash
                    if not quiet:
                        logger.info("copied bundled skill: %s", skill_name)
            except OSError as e:
                if not quiet:
                    logger.warning("failed to copy %s: %s", skill_name, e)
        elif dest.exists():
            origin_hash = manifest.get(skill_name, "")
            user_hash = _dir_hash(dest)
            if not origin_hash:
                manifest[skill_name] = user_hash
                skipped += 1
                continue
            if user_hash != origin_hash:
                user_modified.append(skill_name)
                if not quiet:
                    logger.info("user-modified, skipping: %s", skill_name)
                continue
            if bundled_hash != origin_hash:
                try:
                    backup = dest.with_suffix(".bak")
                    shutil.move(str(dest), str(backup))
                    try:
                        shutil.copytree(skill_src, dest)
                        manifest[skill_name] = bundled_hash
                        updated.append(skill_name)
                        if not quiet:
                            logger.info("updated bundled skill: %s", skill_name)
                        with contextlib.suppress(OSError):
                            _rmtree_writable(backup)
                    except OSError:
                        if backup.exists() and not dest.exists():
                            shutil.move(str(backup), str(dest))
                        raise
                except OSError as e:
                    if not quiet:
                        logger.warning("failed to update %s: %s", skill_name, e)
            else:
                skipped += 1
        else:
            skipped += 1

    cleaned = sorted(set(manifest.keys()) - bundled_names)
    for name in cleaned:
        del manifest[name]

    for desc_md in bundled_dir.rglob("DESCRIPTION.md"):
        dest_desc = skills_dir / desc_md.relative_to(bundled_dir)
        if not dest_desc.exists():
            try:
                dest_desc.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(desc_md, dest_desc)
            except OSError:
                pass

    _write_manifest(manifest)
    if copied or updated:
        _load_protected_builtins.cache_clear()

    return {
        "copied": copied,
        "updated": updated,
        "skipped": skipped,
        "user_modified": user_modified,
        "cleaned": cleaned,
        "suppressed": suppressed_skipped,
        "total_bundled": len(bundled_skills),
    }


def _rmtree_writable(path: Path) -> None:
    def _on_error(func, fpath, exc_info) -> None:
        for target in (os.path.dirname(fpath), fpath):
            with contextlib.suppress(OSError):
                os.chmod(target, stat.S_IRWXU)
        func(fpath)

    shutil.rmtree(path, onerror=_on_error)


def reset_bundled_skill(name: str, restore: bool = False) -> dict:
    manifest = _read_manifest()
    bundled_dir = get_skills_dir()
    bundled_by_name = dict(_discover_bundled_skills(bundled_dir))
    if name not in manifest and name not in bundled_by_name:
        return {
            "ok": False,
            "action": "not_in_manifest",
            "message": f"'{name}' is not a tracked bundled skill. Nothing to reset. (Hub-installed skills use `deskagent skills uninstall`.)",
            "synced": None,
        }
    deleted_user_copy = False
    if restore:
        if name not in bundled_by_name:
            return {
                "ok": False,
                "action": "bundled_missing",
                "message": f"'{name}' has no bundled source — manifest entry preserved but cannot restore from bundled (skill was removed upstream).",
                "synced": None,
            }
        if (dest := _compute_relative_dest(bundled_by_name[name], bundled_dir)).exists():
            try:
                _rmtree_writable(dest)
                deleted_user_copy = True
            except OSError as e:
                return {
                    "ok": False,
                    "action": "not_reset",
                    "message": f"Could not delete user copy at {dest}: {e}. Manifest entry preserved — nothing was changed.",
                    "synced": None,
                }
    if name in manifest:
        del manifest[name]
        _write_manifest(manifest)
    synced = sync_skills(quiet=True)
    action = "restored" if restore else "manifest_cleared"
    message = (
        f"Restored '{name}' from bundled source."
        if restore and deleted_user_copy
        else (
            f"Restored '{name}' (no prior user copy, re-copied from bundled)."
            if restore
            else f"Cleared manifest entry for '{name}'. The next self-update will re-baseline against your current copy and accept upstream changes."
        )
    )
    return {"ok": True, "action": action, "message": message, "synced": synced}


def set_bundled_skills_opt_out(enabled: bool) -> dict:
    marker = DESKAGENT_HOME / NO_BUNDLED_SKILLS_MARKER
    existed = marker.exists()
    try:
        if enabled:
            DESKAGENT_HOME.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                "This profile opted out of bundled-skill seeding (`deskagent skills opt-out`).\nDelete this file to re-enable sync on the next self-update.\n", encoding="utf-8"
            )
            changed = not existed
            message = (
                "Opted out of bundled skills. Future install / update / sync runs will not seed bundled skills into this profile."
                if changed
                else "Already opted out — marker was already present."
            )
        else:
            if existed:
                marker.unlink()
            changed = existed
            message = (
                "Opted back in. The next self-update (or `deskagent skills opt-in --sync`) will re-seed bundled skills." if changed else "Not opted out — no marker to remove."
            )
    except OSError as e:
        return {"ok": False, "changed": False, "marker": str(marker), "message": f"Could not update opt-out marker at {marker}: {e}"}
    return {"ok": True, "changed": changed, "marker": str(marker), "message": message}


def is_bundled_skills_opt_out() -> bool:
    return (DESKAGENT_HOME / NO_BUNDLED_SKILLS_MARKER).exists()


def remove_pristine_bundled_skills(dry_run: bool = False) -> dict:
    manifest = _read_manifest()
    bundled_dir = get_skills_dir()
    bundled_by_name = dict(_discover_bundled_skills(bundled_dir))
    removed, skipped = [], []
    for name, origin_hash in sorted(manifest.items()):
        if (src := bundled_by_name.get(name)) is None:
            skipped.append({"name": name, "reason": "no bundled source (removed upstream)"})
            continue
        if not (dest := _compute_relative_dest(src, bundled_dir)).exists():
            if not dry_run:
                del manifest[name]
            continue
        if _dir_hash(dest) != origin_hash:
            skipped.append({"name": name, "reason": "user-modified (kept)"})
            continue
        if dry_run:
            removed.append(name)
            continue
        try:
            _rmtree_writable(dest)
        except OSError as e:
            skipped.append({"name": name, "reason": f"delete failed: {e}"})
            continue
        del manifest[name]
        removed.append(name)
    if not dry_run and removed:
        _write_manifest(manifest)
    return {
        "ok": True,
        "removed": removed,
        "skipped": skipped,
        "dry_run": dry_run,
        "message": f"{'Would remove' if dry_run else 'Removed'} {len(removed)} pristine bundled skill(s); kept {len(skipped)}.",
    }


if __name__ == "__main__":
    print("Syncing bundled skills into ~/.deskagent/skills/ ...")
    res = sync_skills(quiet=False)
    parts = [f"{len(res['copied'])} new", f"{len(res['updated'])} updated", f"{res['skipped']} unchanged"]
    if res["user_modified"]:
        shown = ", ".join(res["user_modified"][:5]) + (f", +{len(res['user_modified']) - 5} more" if len(res["user_modified"]) > 5 else "")
        parts.append(f"{len(res['user_modified'])} user-modified (kept): {shown}")
    if res["cleaned"]:
        parts.append(f"{len(res['cleaned'])} cleaned from manifest")
    print(f"\nDone: {', '.join(parts)}. {res['total_bundled']} total bundled.")
