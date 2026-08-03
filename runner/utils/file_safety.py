import contextlib
import functools
import os
from pathlib import Path

from .config import cfg_get
from .config import load_config
from .constants import get_deskagent_home
from .constants import IS_WINDOWS

_BLOCKED_PROJECT_ENV_BASENAMES: set[str] = {".env", ".env.local", ".env.development", ".env.production", ".env.test", ".env.staging", ".envrc"}
PROFILE_SCOPED_AREAS = ("skills", "plugins", "cron", "memories")
_SANDBOX_BACKEND_DIR = "sandboxes"
_SANDBOX_HOME_SUFFIX = ("home", ".deskagent")


def _deskagent_home_path() -> Path:
    try:
        return get_deskagent_home()
    except Exception:
        return Path(os.path.expanduser("~/.deskagent"))


_denied_paths_cache: tuple[str, set[str]] | None = None
_denied_prefixes_cache: tuple[str, list[str]] | None = None
_denied_prefixes_norm_cache: tuple[str, list[str]] | None = None


def build_write_denied_paths(home: str) -> set[str]:
    global _denied_paths_cache
    if _denied_paths_cache and _denied_paths_cache[0] == home:
        return _denied_paths_cache[1]
    deskagent, p_home = _deskagent_home_path(), Path(home)
    result = {
        os.path.realpath(p)
        for p in [
            p_home / ".ssh/authorized_keys",
            p_home / ".ssh/id_rsa",
            p_home / ".ssh/id_ed25519",
            p_home / ".ssh/config",
            deskagent / ".env",
            deskagent / "anthropic_oauth.json",
            p_home / ".bashrc",
            p_home / ".zshrc",
            p_home / ".profile",
            p_home / ".bash_profile",
            p_home / ".zprofile",
            p_home / ".netrc",
            p_home / ".pgpass",
            p_home / ".npmrc",
            p_home / ".pypirc",
            p_home / ".git-credentials",
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }
    _denied_paths_cache = (home, result)
    return result


def build_write_denied_prefixes(home: str) -> list[str]:
    global _denied_prefixes_cache
    if _denied_prefixes_cache and _denied_prefixes_cache[0] == home:
        return _denied_prefixes_cache[1]
    p_home = Path(home)
    posix_prefixes = [
        p_home / ".ssh",
        p_home / ".aws",
        p_home / ".gnupg",
        p_home / ".kube",
        "/etc/sudoers.d",
        "/etc/systemd",
        p_home / ".docker",
        p_home / ".azure",
        p_home / ".config/gh",
        p_home / ".config/gcloud",
    ]
    windows_prefixes = [
        Path("C:/Windows/System32"),
        Path("C:/Windows/SysWOW64"),
        Path("C:/Windows/WinSxS"),
        Path("C:/Windows/Boot"),
        Path("C:/Windows/Recovery"),
        # WinSxS is huge and refactor-sensitive — never edit live.
        # Boot/Recovery hold boot-loader / recovery binaries.
        # System32/SysWOW64 are the canonical DLL hosts.
        Path(os.environ.get("SystemRoot", "C:/Windows")),
        Path(os.environ.get("ProgramData", "C:/ProgramData")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        p_home / "AppData/Roaming/Microsoft",
        p_home / "AppData/Local/Microsoft",
    ]
    sources = [*posix_prefixes, *windows_prefixes] if IS_WINDOWS else posix_prefixes
    result = [os.path.realpath(p) + os.sep for p in sources if str(p)]
    _denied_prefixes_cache = (home, result)
    return result


def _build_normalized_prefixes(home: str) -> list[str]:
    """Normalized (lowercase + forward-slash) version of ``build_write_denied_prefixes``.

    On Windows, ``is_write_denied`` needs case/slash-insensitive matching.
    Pre-normalizing avoids calling ``.replace().lower()`` on every prefix
    on every invocation.
    """
    global _denied_prefixes_norm_cache
    if _denied_prefixes_norm_cache and _denied_prefixes_norm_cache[0] == home:
        return _denied_prefixes_norm_cache[1]
    raw = build_write_denied_prefixes(home)
    result = [p.replace("\\", "/").lower() for p in raw]
    _denied_prefixes_norm_cache = (home, result)
    return result


def get_windows_sensitive_prefixes() -> tuple[str, ...]:
    """Canonical lowercase forward-slash Windows system-dir prefixes.

    Shared with ``tools/files/file_tools.py`` so the file-tool write guard
    and the terminal denylist stay in sync — adding a new entry (e.g.
    ``C:/Windows/Tasks``) lives in one place. The trailing ``/`` is
    intentional so ``C:/Windows`` does NOT match the prefix meant for
    ``C:/Windows/System32``; callers should compare with
    ``resolved.startswith(prefix)`` after the same
    ``replace("\\\\", "/").lower()`` normalization the file-tool guard
    performs.
    """
    rel_entries = (
        "windows/system32/",
        "windows/syswow64/",
        "windows/winsxs/",
        "windows/boot/",
        "windows/recovery/",
        "programdata/",
        "program files/",
        "program files (x86)/",
    )
    drives = _enumerate_windows_drives() or ("c",)
    return tuple(f"{drv}:/{rel}" for drv in drives for rel in rel_entries)


@functools.lru_cache(maxsize=1)
def _enumerate_windows_drives() -> tuple[str, ...]:
    """Return mounted Windows drive letters (a-z) as lowercase, no colon.

    Empty on non-Windows; ``("c",)`` on enumeration failure. Result is
    memoized — drive topology doesn't change for the runner's lifetime.
    """
    if not IS_WINDOWS:
        return ()
    try:
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = tuple(chr(ord("a") + i) for i in range(26) if bitmask & (1 << i))
        return drives or ("c",)
    except Exception:
        return ("c",)


def _get_safe_write_root() -> str | None:
    try:
        root = cfg_get(load_config(), "security", "write_safe_root", default="")
        return os.path.realpath(os.path.expanduser(root)) if root else None
    except Exception:
        return None


def is_write_denied(path: str) -> bool:
    try:
        home = os.path.realpath(os.path.expanduser("~"))
        resolved = os.path.realpath(os.path.expanduser(str(path)))
    except Exception:
        return True

    # On Windows the prefix check is slash- and case-sensitive on the raw
    # string. Normalise both sides so a write to ``C:\Windows\System32``,
    # ``c:/windows/system32`` or any trailing-separator variant still hits
    # the denylist — otherwise the agent can bypass it with a different
    # slash style. POSIX is left untouched (no case folding).
    resolved_norm = resolved.replace("\\", "/").lower() if IS_WINDOWS else resolved

    if resolved in build_write_denied_paths(home):
        return True
    if IS_WINDOWS:
        # Pre-normalized prefixes — one pass, no per-prefix replace/lower.
        if any(resolved_norm.startswith(p) for p in _build_normalized_prefixes(home)):
            return True
    elif any(resolved.startswith(p) for p in build_write_denied_prefixes(home)):
        return True

    deskagent_dirs = []
    with contextlib.suppress(Exception):
        deskagent_dirs.append(os.path.realpath(_deskagent_home_path()))

    for base_real in deskagent_dirs:
        try:
            if any(resolved == os.path.realpath(os.path.join(base_real, n)) for n in ("auth.json", "config.yaml", "webhook_subscriptions.json")):
                return True
            for sub in ("mcp-tokens", "pairing"):
                sub_real = os.path.realpath(os.path.join(base_real, sub))
                if resolved == sub_real or resolved.startswith(sub_real + os.sep):
                    return True
        except Exception:
            pass

    return bool((safe_root := _get_safe_write_root()) and not (resolved == safe_root or resolved.startswith(safe_root + os.sep)))


def get_read_block_error(path: str) -> str | None:
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return None

    deskagent_dirs = []
    with contextlib.suppress(Exception):
        deskagent_dirs.append(_deskagent_home_path().resolve())

    for zd in deskagent_dirs:
        for blocked in (zd / "skills/.hub/index-cache", zd / "skills/.hub"):
            try:
                resolved.relative_to(blocked)
                return f"Access denied: {path} is an internal DeskAgent cache file. Use skill_view / skills_list instead."
            except ValueError:
                continue

    credential_file_names = ("auth.json", "auth.lock", "anthropic_oauth.json", ".env", "webhook_subscriptions.json", "auth/google_oauth.json", "cache/bws_cache.json")
    for zd in deskagent_dirs:
        for name in credential_file_names:
            try:
                if resolved == (zd / name).resolve():
                    return f"Access denied: {path} is a DeskAgent credential store and cannot be read directly."
            except Exception:
                pass

    for zd in deskagent_dirs:
        try:
            resolved.relative_to((zd / "mcp-tokens").resolve())
            return f"Access denied: {path} is a DeskAgent MCP token file and cannot be read directly."
        except (ValueError, Exception):
            continue

    if resolved.name in _BLOCKED_PROJECT_ENV_BASENAMES:
        return f"Access denied: {path} is a secret-bearing environment file. Read .env.example instead if checking structure."

    return None


def _resolve_active_profile_name() -> str:
    try:
        deskagent_real = _deskagent_home_path().resolve()
        profiles_root = deskagent_real / "profiles"
        if not profiles_root.is_dir():
            return "default"
        try:
            rel = deskagent_real.relative_to(profiles_root)
        except ValueError:
            return "default"
        if rel.parts:
            return rel.parts[0]
    except (OSError, RuntimeError):
        pass
    return "default"


def classify_cross_profile_target(path: str) -> dict | None:
    try:
        target = Path(os.path.expanduser(str(path))).resolve()
        root_real = _deskagent_home_path().resolve()
        rel = target.relative_to(root_real)
        parts = rel.parts
        if not parts:
            return None
        if parts[0] in PROFILE_SCOPED_AREAS:
            target_profile, area = "default", parts[0]
        elif parts[0] == "profiles" and len(parts) >= 3 and parts[2] in PROFILE_SCOPED_AREAS:
            target_profile, area = parts[1], parts[2]
        else:
            return None
        if target_profile == (active := _resolve_active_profile_name()):
            return None
        return {"active_profile": active, "target_profile": target_profile, "area": area, "target_path": str(target)}
    except (OSError, RuntimeError, ValueError):
        return None


def get_cross_profile_warning(path: str) -> str | None:
    if info := classify_cross_profile_target(path):
        return f"Cross-profile write blocked: {info['target_path']} belongs to profile {info['target_profile']!r} (active: {info['active_profile']!r}). Confirm with user, and retry with cross_profile=True."
    return None


def _find_sandbox_mirror_segments(parts: tuple) -> int | None:
    for i, part in enumerate(parts):
        if part == _SANDBOX_BACKEND_DIR and i + 5 <= len(parts) and parts[i + 3] == _SANDBOX_HOME_SUFFIX[0] and parts[i + 4] == _SANDBOX_HOME_SUFFIX[1]:
            return i + 4
    return None


def classify_sandbox_mirror_target(path: str) -> dict | None:
    try:
        target = Path(os.path.expanduser(str(path))).resolve()
        if (idx := _find_sandbox_mirror_segments(target.parts)) is not None:
            return {
                "target_path": str(target),
                "mirror_root": str(Path(*target.parts[: idx + 1])),
                "inner_path": str(Path(*target.parts[idx + 1 :])) if idx + 1 < len(target.parts) else "",
            }
    except (OSError, RuntimeError):
        pass
    return None


def get_sandbox_mirror_warning(path: str) -> str | None:
    if info := classify_sandbox_mirror_target(path):
        return f"Sandbox-mirror write blocked: {info['target_path']} sits under {info['mirror_root']!r}. Authoritative file is likely {info['inner_path']!r}. Confirm with user, and retry with cross_profile=True."
    return None


def classify_container_mirror_target(path: str, mirror_prefix: str | None = None) -> dict | None:
    if not mirror_prefix:
        return None
    try:
        target, prefix_real = Path(os.path.expanduser(str(path))).resolve(), Path(os.path.expanduser(mirror_prefix)).resolve()
        rel = target.relative_to(prefix_real)
        return {"target_path": str(target), "mirror_root": str(prefix_real), "inner_path": str(Path(*rel.parts)) if rel.parts else ""}
    except (OSError, RuntimeError, ValueError):
        return None


def get_container_mirror_warning(path: str, mirror_prefix: str | None = None) -> str | None:
    if info := classify_container_mirror_target(path, mirror_prefix):
        return f"Container-mirror write blocked: {info['target_path']} sits under {info['mirror_root']!r}. Authoritative file is likely {info['inner_path']!r}. Confirm with user, and retry with cross_profile=True."
    return None
