import functools
import os

# DeskAgent control-plane files: provider credentials, OAuth tokens, HMAC secrets,
# gateway config. Listed by basename — both ~/.deskagent/<name> and profile-scoped
# ~/.deskagent/profiles/<profile>/<name> are blocked.
DESKAGENT_CONTROL_FILE_BASENAMES: tuple[str, ...] = ("auth.json", "auth.lock", "desktop-settings.json", "webhook_subscriptions.json", ".env")

# .env.example is deliberately NOT here — it's a documented-shape substitute.
BLOCKED_PROJECT_ENV_BASENAMES: frozenset[str] = frozenset({".env", ".env.local", ".env.development", ".env.production", ".env.test", ".env.staging", ".envrc"})

_WRITE_DENIED_RELATIVE_PATHS: tuple[tuple[str, ...], ...] = (
    (".ssh", "authorized_keys"),
    (".ssh", "id_rsa"),
    (".ssh", "id_ed25519"),
    (".ssh", "config"),
    (".bashrc",),
    (".zshrc",),
    (".profile",),
    (".bash_profile",),
    (".zprofile",),
    (".netrc",),
    (".pgpass",),
    (".npmrc",),
    (".pypirc",),
    (".git-credentials",),
)

_WRITE_DENIED_ABSOLUTE_PATHS: tuple[str, ...] = ("/etc/sudoers", "/etc/passwd", "/etc/shadow")

_WRITE_DENIED_PREFIXES_RELATIVE: tuple[tuple[str, ...], ...] = ((".ssh",), (".aws",), (".gnupg",), (".kube",), (".docker",), (".azure",), (".config", "gh"), (".config", "gcloud"))

_WRITE_DENIED_PREFIXES_ABSOLUTE: tuple[str, ...] = ("/etc/sudoers.d", "/etc/systemd")
_WRITE_DENIED_DESKAGENT_PREFIXES: tuple[str, ...] = ("mcp-tokens", "pairing", "skills/.hub")


@functools.lru_cache(maxsize=1)
def _deskagent_home() -> str:
    """Canonical ~/.deskagent path. Cached at module load — home rarely moves at runtime."""
    return os.path.realpath(os.path.expanduser("~/.deskagent"))


def _join_real(base: str, *parts: str) -> str:
    return os.path.realpath(os.path.join(base, *parts))


@functools.lru_cache(maxsize=4)
def _write_denied_paths(home: str) -> frozenset[str]:
    """Exact sensitive paths that must never be written."""
    home_real = os.path.realpath(home)
    deskagent_home = _deskagent_home()
    return frozenset(
        {
            *(_join_real(home_real, *parts) for parts in _WRITE_DENIED_RELATIVE_PATHS),
            *(_join_real(deskagent_home, name) for name in DESKAGENT_CONTROL_FILE_BASENAMES),
            *(os.path.realpath(p) for p in _WRITE_DENIED_ABSOLUTE_PATHS),
        }
    )


@functools.lru_cache(maxsize=4)
def _write_denied_prefixes(home: str) -> tuple[str, ...]:
    """Sensitive directory prefixes that must never be written."""
    home_real = os.path.realpath(home)
    deskagent_home = _deskagent_home()
    return tuple(
        p + os.sep
        for p in (
            *(_join_real(home_real, *parts) for parts in _WRITE_DENIED_PREFIXES_RELATIVE),
            *_WRITE_DENIED_PREFIXES_ABSOLUTE,
            *(_join_real(deskagent_home, sub) for sub in _WRITE_DENIED_DESKAGENT_PREFIXES),
        )
    )


def is_write_denied(path: str) -> bool:
    """True when ``path`` falls in the write denylist (after symlink resolution)."""
    try:
        resolved = os.path.realpath(os.path.expanduser(str(path)))
    except Exception:
        return False
    if resolved in _write_denied_paths(os.path.expanduser("~")):
        return True
    return any(resolved.startswith(prefix) for prefix in _write_denied_prefixes(os.path.expanduser("~")))


@functools.lru_cache(maxsize=4)
def _read_block_messages() -> tuple[tuple[str, str], ...]:
    """Pre-resolved (real_path, error_message) pairs for credential files."""
    return tuple((_join_real(_deskagent_home(), name), f"Blocked: cannot read DeskAgent credential file ({name}).") for name in DESKAGENT_CONTROL_FILE_BASENAMES)


@functools.lru_cache(maxsize=4)
def _read_block_prefixes() -> tuple[tuple[str, str], ...]:
    deskagent_home = _deskagent_home()
    return (
        (_join_real(deskagent_home, "mcp-tokens") + os.sep, "Blocked: cannot read DeskAgent credential directory (~/.deskagent/mcp-tokens/)."),
        (_join_real(deskagent_home, "pairing") + os.sep, "Blocked: cannot read DeskAgent credential directory (~/.deskagent/pairing/)."),
    )


def get_read_block_error(path: str) -> str | None:
    """Error message for a denied read target, or None when the path is allowed.

    **Defense-in-depth only** — the terminal tool runs as the same OS user
    with shell access, so a determined attacker can still `cat auth.json`.
    The denylist exists so models that respect tool denials (the empirical
    majority) stop at the error message rather than reaching for the shell.
    """
    try:
        resolved = os.path.realpath(os.path.expanduser(str(path)))
    except Exception:
        return None

    for real_path, message in _read_block_messages():
        if resolved == real_path:
            return message
    for prefix, message in _read_block_prefixes():
        if resolved.startswith(prefix):
            return message

    basename = os.path.basename(resolved).lower()
    if basename in BLOCKED_PROJECT_ENV_BASENAMES:
        return f"Blocked: cannot read project-local environment file ({basename}). Use a redacted substitute (e.g. .env.example) when working with config."

    return None
