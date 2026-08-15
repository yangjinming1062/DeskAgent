import contextlib
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .config import cfg_get, load_config
from .constants import IS_WINDOWS, get_deskagent_home

_BLOCKED_PROJECT_ENV_BASENAMES: set[str] = {".env", ".env.local", ".env.development", ".env.production", ".env.test", ".env.staging", ".envrc"}
PROFILE_SCOPED_AREAS = ("skills", "plugins", "cron", "memories")
_SANDBOX_BACKEND_DIR = "sandboxes"
_SANDBOX_HOME_SUFFIX = ("home", ".deskagent")


def validate_within_dir(path: Path, root: Path) -> str | None:
    """Returns error if path resolves outside root, None if safe."""
    try:
        path.resolve().relative_to(root.resolve())
        return None
    except (ValueError, OSError) as e:
        return f"Path escapes allowed directory: {e}"


def has_traversal_component(path_str: str) -> bool:
    return ".." in Path(path_str).parts


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
    rel_entries = ("windows/system32/", "windows/syswow64/", "windows/winsxs/", "windows/boot/", "windows/recovery/", "programdata/", "program files/", "program files (x86)/")
    drives = _enumerate_windows_drives() or ("c",)
    return tuple(f"{drv}:/{rel}" for drv in drives for rel in rel_entries)


def _enumerate_windows_drives() -> tuple[str, ...]:
    """Return mounted Windows drive letters (a-z) lowercase, no colon."""
    if not IS_WINDOWS:
        return ()
    try:
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


if IS_WINDOWS:
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_SHARE_READ = 1
    _FILE_SHARE_WRITE = 2
    _FILE_SHARE_DELETE = 4
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _VOLUME_NAME_DOS = 0x0
    _FILE_NAME_NORMALIZED = 0x0

    kernel32 = ctypes.windll.kernel32

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]

    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]

    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _strip_device_prefix(path_str: str) -> str:
    """Normalize Windows NT path prefixes (``\\\\?\\``, ``\\\\?\\UNC\\``).

    Slicing is done on the separator-normalized form so forward-slash input
    is handled consistently. ``\\\\.\\`` device paths have no DOS equivalent
    and pass through untouched — stripping that prefix would forge a bogus
    relative path out of e.g. ``\\\\.\\pipe\\x``.
    """
    if not path_str:
        return path_str
    norm = path_str.replace("/", "\\")
    norm_upper = norm.upper()
    if norm_upper.startswith("\\\\?\\UNC\\"):
        return "\\\\" + norm[8:]
    if norm_upper.startswith("\\\\?\\"):
        return norm[4:]
    return path_str


def _split_ads_stream(path_str: str) -> tuple[str, str]:
    """Split Windows path into base file/dir path and NTFS stream suffix.

    Preserves drive letter colon (e.g. 'C:') and only extracts colons
    in subsequent components.
    """
    if not IS_WINDOWS or not path_str:
        return path_str, ""

    norm = path_str.replace("/", "\\")
    drive = ""
    rest = norm
    if len(norm) >= 2 and norm[0].isalpha() and norm[1] == ":":
        drive = norm[:2]
        rest = norm[2:]

    parts = rest.split("\\")
    if not parts:
        return path_str, ""

    last = parts[-1]
    if ":" in last:
        colon_idx = last.index(":")
        base_last = last[:colon_idx]
        stream_suffix = last[colon_idx:]
        parts[-1] = base_last
        base_path = drive + "\\".join(parts)
        return base_path, stream_suffix

    return path_str, ""


def _get_final_path_by_handle(path_str: str) -> str | None:
    """Resolve authoritative canonical path via Win32 GetFinalPathNameByHandleW with dynamic buffer allocation."""
    if not IS_WINDOWS:
        return None
    try:
        h = kernel32.CreateFileW(
            path_str, _FILE_READ_ATTRIBUTES, _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE, None, _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None
        )
        if h == wintypes.HANDLE(-1).value or h == -1:
            return None
        try:
            # Query required buffer size dynamically
            req_len = kernel32.GetFinalPathNameByHandleW(h, None, 0, _VOLUME_NAME_DOS | _FILE_NAME_NORMALIZED)
            if req_len == 0:
                return None
            buf = ctypes.create_unicode_buffer(req_len + 1)
            ret = kernel32.GetFinalPathNameByHandleW(h, buf, req_len + 1, _VOLUME_NAME_DOS | _FILE_NAME_NORMALIZED)
            if ret > 0:
                return _strip_device_prefix(buf.value)
            return None
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return None


def canonicalize_path(path: str) -> str:
    """Authoritative cross-platform path canonicalization.

    On Windows:
    - Strips NT device prefixes (\\\\?\\, \\\\?\\UNC\\).
    - Preserves and isolates NTFS Alternate Data Streams (ADS).
    - Pre-normalizes with normpath to resolve '..' and '.' traversal upfront.
    - Resolves 8.3 short names (PROGRA~1), symlinks, and junctions using
      Win32 GetFinalPathNameByHandleW with dynamic buffer sizing.
    - Backtracks to existing parent directories for uncreated targets.
    - Re-attaches stream suffixes.
    """
    if not path:
        return ""
    expanded = os.path.expanduser(str(path))
    if not IS_WINDOWS:
        return os.path.realpath(expanded)

    cleaned = _strip_device_prefix(expanded)
    base_path, stream_suffix = _split_ads_stream(cleaned)

    # Pre-normalize to resolve '..' and '.' upfront
    norm = os.path.normpath(base_path)

    # Try direct handle resolution on existing file/dir
    resolved = _get_final_path_by_handle(norm)
    if resolved:
        return resolved + stream_suffix

    # For non-existent files/directories: walk up to existing parent directory
    cur = norm
    tail_parts: list[str] = []
    while cur:
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        tail_parts.insert(0, os.path.basename(cur))
        resolved_parent = _get_final_path_by_handle(parent)
        if resolved_parent:
            joined = os.path.join(resolved_parent, *tail_parts)
            return _strip_device_prefix(joined) + stream_suffix
        cur = parent

    # Fallback to os.path.realpath if no parent handle could be queried
    return os.path.realpath(norm) + stream_suffix


def _resolve_long_path(path: str) -> str:
    return canonicalize_path(path)


def is_write_denied(path: str) -> bool:
    try:
        home = canonicalize_path("~")
        resolved = canonicalize_path(str(path))
    except Exception:
        return True

    base_resolved, stream_suffix = _split_ads_stream(resolved)

    # On Windows the prefix check is slash- and case-sensitive on the raw
    # On Windows the prefix and path checks are slash- and case-insensitive.
    # Normalise both sides so a write to ``C:\Windows\System32``,
    # ``c:/windows/system32``, ``~/.BASHRC`` or any variant hits the denylist.
    resolved_norm = resolved.replace("\\", "/").lower() if IS_WINDOWS else resolved
    base_resolved_norm = base_resolved.replace("\\", "/").lower() if IS_WINDOWS else base_resolved

    denied_paths = build_write_denied_paths(home)
    if IS_WINDOWS:
        denied_paths_lower = {p.replace("\\", "/").lower() for p in denied_paths}
        if resolved_norm in denied_paths_lower or base_resolved_norm in denied_paths_lower:
            return True
    else:
        if resolved in denied_paths or base_resolved in denied_paths:
            return True

    if IS_WINDOWS:
        # Pre-normalized prefixes — check both the full path (with any ADS) and base path
        normalized_prefixes = _build_normalized_prefixes(home)
        if any(resolved_norm.startswith(p) or base_resolved_norm.startswith(p) for p in normalized_prefixes):
            return True
        # Also block directory stream tampering like ::$INDEX_ALLOCATION
        if stream_suffix and any(base_resolved_norm == p.rstrip("/") for p in normalized_prefixes):
            return True
    elif any(resolved.startswith(p) or base_resolved.startswith(p) for p in build_write_denied_prefixes(home)):
        return True

    deskagent_dirs = []
    with contextlib.suppress(Exception):
        deskagent_dirs.append(canonicalize_path(str(_deskagent_home_path())))

    for base_real in deskagent_dirs:
        base_norm = base_real.replace("\\", "/").lower() if IS_WINDOWS else base_real
        try:
            for n in ("auth.json", "desktop-settings.json", "webhook_subscriptions.json"):
                target_norm = (base_norm + "/" + n).lower() if IS_WINDOWS else os.path.join(base_real, n)
                if resolved_norm == target_norm or base_resolved_norm == target_norm or resolved == os.path.join(base_real, n) or base_resolved == os.path.join(base_real, n):
                    return True
            for sub in ("mcp-tokens", "pairing"):
                sub_norm = (base_norm + "/" + sub).lower() if IS_WINDOWS else os.path.join(base_real, sub)
                sub_real = os.path.join(base_real, sub)
                if (
                    resolved_norm == sub_norm
                    or resolved_norm.startswith(sub_norm + "/")
                    or base_resolved_norm == sub_norm
                    or base_resolved_norm.startswith(sub_norm + "/")
                    or resolved == sub_real
                    or resolved.startswith(sub_real + os.sep)
                    or base_resolved == sub_real
                    or base_resolved.startswith(sub_real + os.sep)
                ):
                    return True
        except Exception:
            pass

    return bool(
        (safe_root := _get_safe_write_root())
        and not (
            resolved == safe_root
            or resolved.startswith(safe_root + os.sep)
            or base_resolved == safe_root
            or base_resolved.startswith(safe_root + os.sep)
            or (
                IS_WINDOWS
                and (
                    resolved_norm == safe_root.replace("\\", "/").lower()
                    or resolved_norm.startswith(safe_root.replace("\\", "/").lower() + "/")
                    or base_resolved_norm == safe_root.replace("\\", "/").lower()
                    or base_resolved_norm.startswith(safe_root.replace("\\", "/").lower() + "/")
                )
            )
        )
    )


def get_read_block_error(path: str) -> str | None:
    try:
        resolved_str = canonicalize_path(str(path))
        resolved = Path(resolved_str)
        base_resolved_str, _ = _split_ads_stream(resolved_str)
        base_resolved = Path(base_resolved_str)
    except Exception:
        return None

    resolved_norm = resolved_str.replace("\\", "/").lower() if IS_WINDOWS else resolved_str
    base_resolved_norm = base_resolved_str.replace("\\", "/").lower() if IS_WINDOWS else base_resolved_str

    deskagent_dirs = []
    with contextlib.suppress(Exception):
        deskagent_dirs.append(Path(canonicalize_path(str(_deskagent_home_path()))))

    for zd in deskagent_dirs:
        zd_norm = str(zd).replace("\\", "/").lower() if IS_WINDOWS else str(zd)
        for blocked_sub in ("skills/.hub/index-cache", "skills/.hub"):
            blocked_target = zd_norm + "/" + blocked_sub if IS_WINDOWS else str(zd / blocked_sub)
            if (
                resolved_norm == blocked_target
                or resolved_norm.startswith(blocked_target + "/")
                or base_resolved_norm == blocked_target
                or base_resolved_norm.startswith(blocked_target + "/")
            ):
                return f"Access denied: {path} is an internal DeskAgent cache file. Use skill_view / skills_list instead."

    credential_file_names = ("auth.json", "auth.lock", "anthropic_oauth.json", ".env", "webhook_subscriptions.json", "auth/google_oauth.json", "cache/bws_cache.json")
    for zd in deskagent_dirs:
        zd_norm = str(zd).replace("\\", "/").lower() if IS_WINDOWS else str(zd)
        for name in credential_file_names:
            target_norm = zd_norm + "/" + name if IS_WINDOWS else str((zd / name).resolve())
            if resolved_norm == target_norm or base_resolved_norm == target_norm or resolved == (zd / name).resolve() or base_resolved == (zd / name).resolve():
                return f"Access denied: {path} is a DeskAgent credential store and cannot be read directly."

    for zd in deskagent_dirs:
        zd_norm = str(zd).replace("\\", "/").lower() if IS_WINDOWS else str(zd)
        mcp_target = zd_norm + "/mcp-tokens"
        if resolved_norm == mcp_target or resolved_norm.startswith(mcp_target + "/") or base_resolved_norm == mcp_target or base_resolved_norm.startswith(mcp_target + "/"):
            return f"Access denied: {path} is a DeskAgent MCP token file and cannot be read directly."

    name_check = resolved.name.lower() if IS_WINDOWS else resolved.name
    base_name_check = base_resolved.name.lower() if IS_WINDOWS else base_resolved.name
    if name_check in _BLOCKED_PROJECT_ENV_BASENAMES or base_name_check in _BLOCKED_PROJECT_ENV_BASENAMES:
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
        target, prefix_real = (Path(os.path.expanduser(str(path))).resolve(), Path(os.path.expanduser(mirror_prefix)).resolve())
        rel = target.relative_to(prefix_real)
        return {"target_path": str(target), "mirror_root": str(prefix_real), "inner_path": str(Path(*rel.parts)) if rel.parts else ""}
    except (OSError, RuntimeError, ValueError):
        return None


def get_container_mirror_warning(path: str, mirror_prefix: str | None = None) -> str | None:
    if info := classify_container_mirror_target(path, mirror_prefix):
        return f"Container-mirror write blocked: {info['target_path']} sits under {info['mirror_root']!r}. Authoritative file is likely {info['inner_path']!r}. Confirm with user, and retry with cross_profile=True."
    return None
