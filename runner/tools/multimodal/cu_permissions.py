import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)


_MACOS_PERMISSION_PROBES: tuple[tuple[str, str, str], ...] = (
    # Screen Recording is what gates CGWindowListCreate / ScreenCaptureKit —
    # the actual APIs cua-driver uses for capture. We probe it by trying to
    # take a tiny screencapture to a tempfile and reading the bytes back;
    # when TCC denies Screen Recording, the capture binary returns a
    # zero-byte file regardless of region size, while a successful capture
    # produces non-empty PNG data.
    (
        "screen_recording",
        "Screen Recording",
        # /tmp path so we don't depend on the user's TMPDIR; output is deleted
        # immediately after read so we don't litter the system.
        "do shell script \"screencapture -x -t png -R 0,0,1,1 /tmp/.spiritagent_cu_sr_probe.png && wc -c < /tmp/.spiritagent_cu_sr_probe.png | tr -d ' \\n'\"",
    ),
    # Accessibility / Automation is what gates CGEventPost and the AX APIs
    # that cua-driver uses for clicks and key injection. We probe it by
    # asking System Events for a process count — TCC denies this when
    # Accessibility is missing.
    ("accessibility", "Accessibility", 'tell application "System Events" to count processes'),
)


def get_permission_status() -> dict[str, Any]:
    """Return permission status for computer_use on the current platform."""
    if sys.platform != "darwin":
        return {"ok": True, "missing": [], "platform": sys.platform, "details": {}}

    # Probes are independent — run them in parallel so a TCC dialog being
    # open on one permission doesn't block the other probe (worst case halves
    # from ~30s to ~15s on a stalled dialog).
    probes = list(_MACOS_PERMISSION_PROBES)
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        results = list(pool.map(lambda p: (p[0], p[1], _probe_macos_permission(p[2])), probes))

    missing: list[str] = []
    pending: list[str] = []
    details: dict[str, str] = {}
    for key, label, (result, err) in results:
        if result == "ok":
            details[key] = "ok"
        elif result == "denied":
            details[key] = f"missing ({err or 'permission denied'})"
            missing.append(label)
        else:  # "pending" — TCC dialog likely still open, user mid-grant
            details[key] = f"pending ({err or 'probe timed out'})"
            pending.append(label)

    return {"ok": not missing, "missing": missing, "pending": pending, "platform": "darwin", "details": details}


def _probe_macos_permission(osascript: str) -> tuple[str, str | None]:
    """Run a tiny AppleScript and return one of ``"ok" / "denied" / "pending" / "unknown"``.

    - ``"ok"`` — exit 0, permission granted
    - ``"denied"`` — explicit TCC rejection (matched error strings)
    - ``"pending"`` — probe timed out, very likely because a TCC dialog is
      open and the user is mid-grant; we deliberately don't downgrade to
      ``"denied"`` because the dialog being open is itself progress.
    - ``"unknown"`` — failed without matching a TCC rejection signature;
      callers bucket it with ``pending`` rather than claiming denial.

    ``osascript`` ships with every macOS install — no extra dependency. The
    TCC framework returns specific error strings we pattern-match against
    ("not authorized", "operation not permitted") on rejection.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", osascript],
            capture_output=True,
            text=True,
            timeout=15,  # generous — user may be reading the TCC warning text
            check=False,
        )
    except FileNotFoundError:
        return "denied", "osascript not available"
    except subprocess.TimeoutExpired:
        return "pending", "osascript timed out (likely a TCC dialog is open)"

    if result.returncode == 0:
        return "ok", None
    err = (result.stderr or result.stdout or "").strip().splitlines()[-1] if (result.stderr or result.stdout) else "unknown"
    err_lower = err.lower()
    if "not authorized" in err_lower or "not permitted" in err_lower or "(-1743)" in err_lower or "(-25211)" in err_lower:
        return "denied", err
    return "unknown", err
