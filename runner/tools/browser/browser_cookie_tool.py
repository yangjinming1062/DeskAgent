import json
import logging
from typing import Any

from ..registry import registry
from .browser_supervisor import SUPERVISOR_REGISTRY

logger = logging.getLogger(__name__)


def _no_supervisor() -> str:
    return json.dumps({"success": False, "error": "No CDP supervisor is attached to this task. Call browser_navigate first."}, ensure_ascii=False)


BROWSER_COOKIES_GET_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_get",
    "description": (
        "Read all cookies visible to the current page, optionally filtered by URL. "
        "Backed by CDP ``Network.getCookies``. Read-only — does not mutate state. "
        "Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": ("Optional URL whose cookies to retrieve (e.g. 'https://example.com'). If omitted, returns all cookies for the current browser context."),
            }
        },
        "required": [],
    },
}


def browser_cookies_get(url: str | None = None, task_id: str | None = None) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id or "default")) is None:
        return _no_supervisor()
    params: dict[str, Any] = {}
    if url:
        params["urls"] = [url]
    res = supervisor.send_cdp("Network.getCookies", params)
    if not res.get("ok"):
        return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
    cookies = res.get("result", {}).get("cookies", [])
    return json.dumps({"success": True, "count": len(cookies), "cookies": cookies}, ensure_ascii=False)


registry.register_tool("browser_cookies_get", schema=BROWSER_COOKIES_GET_SCHEMA)(lambda args, **kw: browser_cookies_get(url=args.get("url"), task_id=kw.get("task_id")))


BROWSER_COOKIES_SET_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_set",
    "description": (
        "Set a cookie via CDP ``Network.setCookie``. Useful for re-establishing session state after restart, or injecting auth tokens for testing. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Cookie name."},
            "value": {"type": "string", "description": "Cookie value."},
            "domain": {"type": "string", "description": "Cookie domain (e.g. 'example.com')."},
            "path": {"type": "string", "default": "/", "description": "Cookie path (default '/')."},
            "expires": {"type": "number", "description": "Expiration as UNIX timestamp. Omit for a session cookie."},
            "httpOnly": {"type": "boolean", "default": False, "description": "If true, the cookie is not accessible via JavaScript."},
            "secure": {"type": "boolean", "default": False, "description": "If true, the cookie is only sent over HTTPS."},
            "sameSite": {"type": "string", "enum": ["Strict", "Lax", "None"], "description": "SameSite policy. Defaults to None (browser default)."},
        },
        "required": ["name", "value", "domain"],
    },
}


def browser_cookies_set(
    name: str,
    value: str,
    domain: str,
    path: str = "/",
    expires: float | None = None,
    http_only: bool = False,
    secure: bool = False,
    same_site: str | None = None,
    task_id: str | None = None,
) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id or "default")) is None:
        return _no_supervisor()
    params: dict[str, Any] = {"name": name, "value": value, "domain": domain, "path": path, "httpOnly": http_only, "secure": secure}
    if expires is not None:
        params["expires"] = expires
    if same_site is not None:
        params["sameSite"] = same_site
    res = supervisor.send_cdp("Network.setCookie", params)
    if not res.get("ok"):
        return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps({"success": True, "name": name, "domain": domain}, ensure_ascii=False)


registry.register_tool("browser_cookies_set", schema=BROWSER_COOKIES_SET_SCHEMA)(
    lambda args, **kw: browser_cookies_set(
        name=args.get("name", ""),
        value=args.get("value", ""),
        domain=args.get("domain", ""),
        path=args.get("path", "/"),
        expires=args.get("expires"),
        http_only=args.get("httpOnly", False),
        secure=args.get("secure", False),
        same_site=args.get("sameSite"),
        task_id=kw.get("task_id"),
    )
)


BROWSER_COOKIES_CLEAR_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_clear",
    "description": (
        "Clear browser cookies and/or storage for the current origin via CDP. "
        "By default clears both session cookies and storage data so subsequent "
        "navigation has no persisted state from earlier interactions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session": {"type": "boolean", "default": True, "description": "If true (default), clear all session cookies."},
            "storage": {"type": "boolean", "default": True, "description": "If true (default), clear localStorage / sessionStorage / indexedDB."},
        },
        "required": [],
    },
}


def browser_cookies_clear(session: bool = True, storage: bool = True, task_id: str | None = None) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id or "default")) is None:
        return _no_supervisor()
    actions: list[str] = []
    if session:
        if not supervisor.send_cdp("Network.clearBrowserCookies", {}).get("ok"):
            return json.dumps({"success": False, "error": "Network.clearBrowserCookies failed"}, ensure_ascii=False)
        actions.append("session_cookies")
    if storage:
        # ``clearDataForOrigin`` takes the current origin's scheme://host:port
        # — ``storageTypes`` defaults to all (cookies+localStorage+indexedDB+...).
        # Reading ``location.origin`` keeps the call idempotent across
        # navigations and matches Chrome's documentation.
        if not supervisor.send_cdp("Storage.clearDataForOrigin", {"origin": "*", "storageTypes": "all"}).get("ok"):
            return json.dumps({"success": False, "error": "Storage.clearDataForOrigin failed"}, ensure_ascii=False)
        actions.append("storage")
    return json.dumps({"success": True, "cleared": actions}, ensure_ascii=False)


registry.register_tool("browser_cookies_clear", schema=BROWSER_COOKIES_CLEAR_SCHEMA)(
    lambda args, **kw: browser_cookies_clear(session=args.get("session", True), storage=args.get("storage", True), task_id=kw.get("task_id"))
)


BROWSER_STORAGE_GET_SCHEMA: dict[str, Any] = {
    "name": "browser_storage_get",
    "description": (
        "Read the value of a localStorage or sessionStorage entry from a "
        "specific origin. Backed by CDP ``DOMStorage.getDOMStorageItems`` + "
        "``getItems``. Read-only. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Storage entry name."},
            "origin": {"type": "string", "description": "Origin whose storage to read (e.g. 'https://example.com')."},
            "kind": {"type": "string", "enum": ["localStorage", "sessionStorage"], "default": "localStorage", "description": "Which storage tier to read (default localStorage)."},
        },
        "required": ["key", "origin"],
    },
}


def browser_storage_get(key: str, origin: str, kind: str = "localStorage", task_id: str | None = None) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id or "default")) is None:
        return _no_supervisor()
    res = supervisor.send_cdp("DOMStorage.getDOMStorageItems", {"storageId": {"securityOrigin": origin, "isLocalStorage": kind == "localStorage"}})
    if not res.get("ok"):
        return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
    items = res.get("result", {}).get("entries", [])
    for entry in items:
        if entry[0] == key:
            return json.dumps({"success": True, "key": key, "value": entry[1], "kind": kind}, ensure_ascii=False)
    return json.dumps({"success": False, "error": f"key {key!r} not found in {kind} for {origin}"}, ensure_ascii=False)


registry.register_tool("browser_storage_get", schema=BROWSER_STORAGE_GET_SCHEMA)(
    lambda args, **kw: browser_storage_get(key=args.get("key", ""), origin=args.get("origin", ""), kind=args.get("kind", "localStorage"), task_id=kw.get("task_id"))
)


BROWSER_STORAGE_SET_SCHEMA: dict[str, Any] = {
    "name": "browser_storage_set",
    "description": (
        "Set the value of a localStorage / sessionStorage entry for a specific "
        "origin. Backed by CDP ``DOMStorage.setDOMStorageItem``. Mutates page "
        "state. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Storage entry name."},
            "value": {"type": "string", "description": "Value to store."},
            "origin": {"type": "string", "description": "Target origin (e.g. 'https://example.com')."},
            "kind": {"type": "string", "enum": ["localStorage", "sessionStorage"], "default": "localStorage", "description": "Which storage tier to write (default localStorage)."},
        },
        "required": ["key", "value", "origin"],
    },
}


def browser_storage_set(key: str, value: str, origin: str, kind: str = "localStorage", task_id: str | None = None) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id or "default")) is None:
        return _no_supervisor()
    res = supervisor.send_cdp("DOMStorage.setDOMStorageItem", {"storageId": {"securityOrigin": origin, "isLocalStorage": kind == "localStorage"}, "key": key, "value": value})
    if not res.get("ok"):
        return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps({"success": True, "key": key, "kind": kind, "origin": origin}, ensure_ascii=False)


registry.register_tool("browser_storage_set", schema=BROWSER_STORAGE_SET_SCHEMA)(
    lambda args, **kw: browser_storage_set(
        key=args.get("key", ""), value=args.get("value", ""), origin=args.get("origin", ""), kind=args.get("kind", "localStorage"), task_id=kw.get("task_id")
    )
)
