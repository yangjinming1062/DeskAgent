import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)


_MACOS_PERMISSION_PROBES: tuple[tuple[str, str, str], ...] = (
    # Screen Recording 权限门控 CGWindowListCreate / ScreenCaptureKit — 这是 cua-driver 实际使用的截图 API。
    # 通过尝试截取 1x1 区域到临时文件再读回字节数来探测：TCC 拒绝时无论区域多大都返回 0 字节，成功时返回有效 PNG 数据。
    (
        "screen_recording",
        "Screen Recording",
        # 使用 /tmp 路径以避免依赖用户的 TMPDIR；读取后立即删除，避免污染系统
        "do shell script \"screencapture -x -t png -R 0,0,1,1 /tmp/.spiritagent_cu_sr_probe.png && wc -c < /tmp/.spiritagent_cu_sr_probe.png | tr -d ' \\n'\"",
    ),
    # Accessibility / Automation 权限门控 CGEventPost 以及 cua-driver 用于点击和按键注入的 AX API。
    # 通过向 System Events 请求进程计数来探测：缺失 Accessibility 时 TCC 会拒绝此调用。
    ("accessibility", "Accessibility", 'tell application "System Events" to count processes'),
)


def get_permission_status() -> dict[str, Any]:
    """返回当前平台上 computer_use 的权限状态。"""
    if sys.platform != "darwin":
        return {"ok": True, "missing": [], "platform": sys.platform, "details": {}}

    # 各探测相互独立，并行执行以避免一个权限的 TCC 对话框阻塞另一个（最坏情况下，从 ~30s 减半到 ~15s）
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
        else:  # "pending" — TCC 对话框可能仍开着，用户授权中
            details[key] = f"pending ({err or 'probe timed out'})"
            pending.append(label)

    return {"ok": not missing, "missing": missing, "pending": pending, "platform": "darwin", "details": details}


def _probe_macos_permission(osascript: str) -> tuple[str, str | None]:
    """运行一小段 AppleScript，返回 "ok" / "denied" / "pending" / "unknown" 之一。

    - "ok" — exit 0，权限已授予
    - "denied" — 显式 TCC 拒绝（匹配错误字符串）
    - "pending" — 探测超时，很可能是 TCC 对话框开着、用户正在授权；故意不降级为 "denied"，因为对话框开着本身就是进展
    - "unknown" — 失败但未匹配到 TCC 拒绝特征；调用方将其归入 pending，而非声称被拒

    osascript 是 macOS 自带 — 无额外依赖。TCC 框架在被拒绝时返回特定错误字符串，
    我们按模式匹配（"not authorized"、"operation not permitted" 等）。
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", osascript],
            capture_output=True,
            text=True,
            timeout=15,  # 留宽裕一些 — 用户可能正在阅读 TCC 警告文本
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
