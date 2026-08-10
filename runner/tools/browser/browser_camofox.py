import base64
import ipaddress
import json
import logging
import re
import threading
import uuid
from typing import Any
from urllib.parse import SplitResult
from urllib.parse import urlparse
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import requests
from utils import call_llm
from utils import cfg_get
from utils import get_deskagent_home
from utils import load_config
from utils import redact_sensitive_text

from ..registry import tool_error
from .browser_camofox_state import get_camofox_identity
from .helpers import _extract_relevant_content
from .helpers import _truncate_snapshot
from .helpers import SNAPSHOT_SUMMARIZE_THRESHOLD

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_vnc_url: str | None = None
_vnc_url_checked = False


def get_camofox_url() -> str:
    val = cfg_get(load_config(), "browser", "camofox", "url")
    return str(val).rstrip("/") if val else ""


def is_camofox_mode() -> bool:
    cdp_override = str(cfg_get(load_config(), "browser", "cdp_url", default="")).strip()
    return not cdp_override and bool(get_camofox_url())


def check_camofox_available() -> bool:
    global _vnc_url, _vnc_url_checked
    if not (url := get_camofox_url()):
        return False
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        if resp.status_code == 200 and not _vnc_url_checked:
            if isinstance(vnc_port := resp.json().get("vncPort"), int) and 1 <= vnc_port <= 65535:
                _vnc_url = f"http://{urlparse(url).hostname or 'localhost'}:{vnc_port}"
            _vnc_url_checked = True
        return resp.status_code == 200
    except Exception:
        return False


def get_vnc_url() -> str | None:
    if not _vnc_url_checked:
        check_camofox_available()
    return _vnc_url


def _get_camofox_config() -> dict[str, Any]:
    try:
        cfg = cfg_get(load_config(), "browser", "camofox", default={})
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logger.warning("camofox config check failed, defaulting to disabled: %s", exc)
        return {}


def _camofox_identity_override(task_id: str | None, camofox_cfg: dict[str, Any]) -> dict[str, str] | None:
    user_id = str(camofox_cfg.get("user_id") or "").strip()
    session_key = str(camofox_cfg.get("session_key") or "").strip() or f"task_{(task_id or 'default')[:16]}"
    if not user_id:
        return None
    return {"user_id": user_id, "session_key": session_key}


def _adopt_existing_tab_enabled(camofox_cfg: dict[str, Any]) -> bool:
    if (val := camofox_cfg.get("adopt_existing_tab")) is not None:
        return bool(val)
    return False


def _loopback_rewrite_enabled(camofox_cfg: dict[str, Any]) -> bool:
    if (val := camofox_cfg.get("rewrite_loopback_urls")) is not None:
        return bool(val)
    return False


def _loopback_rewrite_host(camofox_cfg: dict[str, Any]) -> str:
    val = str(camofox_cfg.get("loopback_host_alias") or "").strip()
    return val or "host.docker.internal"


def _is_loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    if (host := hostname.strip().strip("[]").lower()) in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _rewrite_loopback_url_for_camofox(url: str) -> tuple[str, dict[str, str] | None]:
    camofox_cfg = _get_camofox_config()
    if not _loopback_rewrite_enabled(camofox_cfg):
        return url, None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url, None
    if parsed.scheme not in {"http", "https"} or not _is_loopback_hostname(parsed.hostname) or not (alias := _loopback_rewrite_host(camofox_cfg)):
        return url, None

    userinfo = f"{parsed.username}{f':{parsed.password}' if parsed.password else ''}@" if parsed.username else ""
    host_part = f"[{alias}]" if ":" in alias and not alias.startswith("[") else alias
    port_part = f":{parsed.port}" if parsed.port else ""
    rewritten = urlunsplit(SplitResult(parsed.scheme, f"{userinfo}{host_part}{port_part}", parsed.path, parsed.query, parsed.fragment))
    return rewritten, {"from": parsed.hostname or "", "to": alias, "original_url": url, "rewritten_url": rewritten}


_sessions: dict[str, dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()


def _adopt_existing_tab(session: dict[str, Any]) -> dict[str, Any]:
    if session.get("tab_id") or not session.get("adopt_existing_tab") or not get_camofox_url():
        return session
    try:
        tabs = _get("/tabs", params={"userId": session["user_id"]}, timeout=5).get("tabs", [])
        if isinstance(tabs, list) and tabs:
            session_key = session.get("session_key")
            candidates = [t for t in tabs if isinstance(t, dict) and t.get("listItemId") == session_key] or [t for t in tabs if isinstance(t, dict)]
            if candidates and isinstance(tab_id := candidates[-1].get("tabId"), str) and tab_id:
                session["tab_id"] = tab_id
                logger.debug("Adopted existing Camofox tab %s for %s", tab_id, session.get("user_id"))
    except Exception as exc:
        logger.debug("Camofox tab adoption failed for %s: %s", session.get("user_id"), exc)
    return session


def _get_session(task_id: str | None) -> dict[str, Any]:
    task_id = task_id or "default"
    with _SESSIONS_LOCK:
        if task_id not in _sessions:
            camofox_cfg = _get_camofox_config()
            if identity := _camofox_identity_override(task_id, camofox_cfg):
                _sessions[task_id] = {
                    "user_id": identity["user_id"],
                    "tab_id": None,
                    "session_key": identity["session_key"],
                    "managed": True,
                    "adopt_existing_tab": _adopt_existing_tab_enabled(camofox_cfg),
                }
            elif bool(camofox_cfg.get("managed_persistence")):
                identity = get_camofox_identity(task_id)
                _sessions[task_id] = {
                    "user_id": identity["user_id"],
                    "tab_id": None,
                    "session_key": identity["session_key"],
                    "managed": True,
                    "adopt_existing_tab": _adopt_existing_tab_enabled(camofox_cfg),
                }
            else:
                _sessions[task_id] = {
                    "user_id": f"deskagent_{uuid.uuid4().hex[:10]}",
                    "tab_id": None,
                    "session_key": f"task_{task_id[:16]}",
                    "managed": False,
                    "adopt_existing_tab": False,
                }
        return _adopt_existing_tab(_sessions[task_id])


def _ensure_tab(task_id: str | None, url: str = "about:blank") -> dict[str, Any]:
    session = _get_session(task_id)
    if session["tab_id"]:
        return session
    base = get_camofox_url()
    resp = requests.post(
        f"{base}/tabs",
        json={
            "userId": session["user_id"],
            "sessionKey": session["session_key"],
            "url": url,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    session["tab_id"] = resp.json().get("tabId")
    return session


def _drop_session(task_id: str | None) -> dict[str, Any] | None:
    task_id = task_id or "default"
    with _SESSIONS_LOCK:
        return _sessions.pop(task_id, None)


def camofox_soft_cleanup(task_id: str | None = None) -> bool:
    camofox_cfg = _get_camofox_config()
    if bool(camofox_cfg.get("managed_persistence")) or _camofox_identity_override(task_id, camofox_cfg):
        _drop_session(task_id)
        logger.debug("Camofox soft cleanup for task %s (managed persistence)", task_id)
        return True
    return False


def _post(path: str, body: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    resp = requests.post(f"{get_camofox_url()}{path}", json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict | None = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    resp = requests.get(f"{get_camofox_url()}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get_raw(path: str, params: dict | None = None, timeout: int = _DEFAULT_TIMEOUT) -> requests.Response:
    resp = requests.get(f"{get_camofox_url()}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


def _delete(path: str, body: dict | None = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    resp = requests.delete(f"{get_camofox_url()}{path}", json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def camofox_navigate(url: str, task_id: str | None = None) -> str:
    try:
        browser_url, rewrite_info = _rewrite_loopback_url_for_camofox(url)
        session = _get_session(task_id)
        if not session["tab_id"]:
            session = _ensure_tab(task_id, browser_url)
            data = {"ok": True, "url": browser_url}
        else:
            data = _post(
                f"/tabs/{session['tab_id']}/navigate",
                {"userId": session["user_id"], "url": browser_url},
                timeout=60,
            )
        result = {
            "success": True,
            "url": data.get("url", browser_url),
            "title": data.get("title", ""),
        }
        if rewrite_info:
            result.update({
                "requested_url": url,
                "url_rewrite": rewrite_info,
                "warning": f"Rewrote loopback URL for Docker-hosted Camofox: {rewrite_info['from']} -> {rewrite_info['to']}",
            })
        if vnc := get_vnc_url():
            result.update({"vnc_url": vnc, "vnc_hint": "Browser is visible via VNC. Share this link with the user so they can watch the browser live."})
        try:
            snap_data = _get(f"/tabs/{session['tab_id']}/snapshot", params={"userId": session["user_id"]})
            snapshot_text = snap_data.get("snapshot", "")
            if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
                snapshot_text = _truncate_snapshot(snapshot_text)
            result["snapshot"] = snapshot_text
            result["element_count"] = snap_data.get("refsCount", 0)
        except Exception:
            pass
        return json.dumps(result)
    except requests.HTTPError as e:
        return tool_error(f"Navigation failed: {e}", success=False)
    except requests.ConnectionError:
        return json.dumps({"success": False, "error": f"Cannot connect to Camofox at {get_camofox_url()}. Is the server running? Start it first."})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_snapshot(full: bool = False, task_id: str | None = None, user_task: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        data = _get(f"/tabs/{session['tab_id']}/snapshot", params={"userId": session["user_id"]})
        snapshot = data.get("snapshot", "")
        if len(snapshot) > SNAPSHOT_SUMMARIZE_THRESHOLD:
            snapshot = _extract_relevant_content(snapshot, user_task) if user_task else _truncate_snapshot(snapshot)
        return json.dumps({"success": True, "snapshot": snapshot, "element_count": data.get("refsCount", 0)})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_click(ref: str, task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        clean_ref = ref.lstrip("@")
        data = _post(f"/tabs/{session['tab_id']}/click", {"userId": session["user_id"], "ref": clean_ref})
        return json.dumps({"success": True, "clicked": clean_ref, "url": data.get("url", "")})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_type(ref: str, text: str, task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        clean_ref = ref.lstrip("@")
        _post(f"/tabs/{session['tab_id']}/type", {"userId": session["user_id"], "ref": clean_ref, "text": text})
        return json.dumps({"success": True, "typed": text, "element": clean_ref})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_scroll(direction: str, task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        _post(f"/tabs/{session['tab_id']}/scroll", {"userId": session["user_id"], "direction": direction})
        return json.dumps({"success": True, "scrolled": direction})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_back(task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        data = _post(f"/tabs/{session['tab_id']}/back", {"userId": session["user_id"]})
        return json.dumps({"success": True, "url": data.get("url", "")})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_press(key: str, task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        _post(f"/tabs/{session['tab_id']}/press", {"userId": session["user_id"], "key": key})
        return json.dumps({"success": True, "pressed": key})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_close(task_id: str | None = None) -> str:
    try:
        if session := _drop_session(task_id):
            _delete(f"/sessions/{session['user_id']}")
        return json.dumps({"success": True, "closed": True})
    except Exception as e:
        return json.dumps({"success": True, "closed": True, "warning": str(e)})


def camofox_get_images(task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        data = _get(f"/tabs/{session['tab_id']}/snapshot", params={"userId": session["user_id"]})
        images = []
        lines = data.get("snapshot", "").split("\n")
        for i, line in enumerate(lines):
            if (stripped := line.strip()).startswith(("- img ", "img ")):
                alt = m.group(1) if (m := re.search(r'img\s+"([^"]*)"', stripped)) else ""
                src = m.group(1) if i + 1 < len(lines) and (m := re.search(r"/url:\s*(\S+)", lines[i + 1].strip())) else ""
                if alt or src:
                    images.append({"src": src, "alt": alt})
        return json.dumps({"success": True, "images": images, "count": len(images)})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_vision(question: str, annotate: bool = False, task_id: str | None = None) -> str:
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)
        resp = _get_raw(f"/tabs/{session['tab_id']}/screenshot", params={"userId": session["user_id"]})
        screenshots_dir = get_deskagent_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")
        with open(screenshot_path, "wb") as f:
            f.write(resp.content)

        annotation_context = ""
        if annotate:
            try:
                snap_data = _get(f"/tabs/{session['tab_id']}/snapshot", params={"userId": session["user_id"]})
                annotation_context = f"\n\nAccessibility tree (element refs for interaction):\n{snap_data.get('snapshot', '')[:3000]}"
            except Exception:
                pass

        annotation_context = redact_sensitive_text(annotation_context)
        vision_prompt = f"Analyze this browser screenshot and answer: {question}{annotation_context}"

        try:
            from ..multimodal.helpers import _resolve_vision_params

            _vision_timeout, _vision_temperature = _resolve_vision_params()
        except Exception:
            _vision_timeout, _vision_temperature = 120.0, 0.1

        response = call_llm(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(resp.content).decode('utf-8')}"}},
                    ],
                }
            ],
            task="vision",
            temperature=_vision_temperature,
            timeout=_vision_timeout,
        )
        analysis = redact_sensitive_text((response.choices[0].message.content or "").strip() if response.choices else "")
        return json.dumps({"success": True, "analysis": analysis, "screenshot_path": screenshot_path})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_console(clear: bool = False, task_id: str | None = None) -> str:
    return json.dumps({
        "success": True,
        "console_messages": [],
        "js_errors": [],
        "total_messages": 0,
        "total_errors": 0,
        "note": "Console log capture is not available with the Camofox backend. Use browser_snapshot or browser_vision.",
    })
