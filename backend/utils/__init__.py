from .auth import BEARER_SCHEME
from .auth import create_access_token
from .auth import create_admin_token
from .auth import decode_access_token
from .auth import get_current_admin_token
from .auth import get_current_session
from .auth import hash_password
from .auth import verify_password
from .background import BackgroundTask
from .background import fetch_public_ip
from .background import get_or_404
from .background import list_response
from .db import ENGINE
from .db import get_db
from .db import SESSION_LOCAL
from .db import session_scope
from .hashing import normalize_sha512
from .hashing import sha256_hex
from .hashing import sha512_b64
from .json_helpers import apply_partial
from .json_helpers import safe_json_loads
from .json_helpers import tool_error
from .text import as_bool
from .text import coerce_int
from .text import FALSY_STRINGS
from .text import naive_utc_now
from .text import positive_int
from .text import TRUTHY_STRINGS
from .text import unquote_user_setting
from .types import approx_message_tokens
from .types import is_finite_number


def fingerprint_api_key(api_key: str | None) -> str:
    """Stable, non-reversible display tag for an LLM API key.

    Used by ``GET /api/user/model-config`` so the renderer can show the user
    which key is on file without ever sending the raw secret over the wire.
    Returns ``"<empty>"`` for missing keys and ``"<short>"`` for keys
    shorter than the slicing window — these are almost always typos or
    misconfigurations, and we refuse to leak a 1-2 char key.
    """
    if not api_key:
        return "<empty>"
    # Format: first 3 chars + "…" + last 2 chars, e.g. "sk-…7a".
    # Requires at least 8 chars so a truncated / misconfigured short
    # value doesn't surface as a near-full-key fingerprint.
    if len(api_key) < 8:
        return "<short>"
    return f"{api_key[:3]}…{api_key[-2:]}"


__all__ = [
    # db
    "ENGINE",
    "SESSION_LOCAL",
    # auth
    "BEARER_SCHEME",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_admin_token",
    "decode_access_token",
    "get_current_admin_token",
    "get_current_session",
    # background
    "BackgroundTask",
    "fetch_public_ip",
    "list_response",
    "get_or_404",
    # db
    "session_scope",
    "get_db",
    # hashing
    "sha256_hex",
    "sha512_b64",
    "normalize_sha512",
    # json_helpers
    "apply_partial",
    "safe_json_loads",
    "tool_error",
    # text
    "naive_utc_now",
    "as_bool",
    "positive_int",
    "coerce_int",
    "unquote_user_setting",
    # types
    "is_finite_number",
    "approx_message_tokens",
    # misc
    "fingerprint_api_key",
]
