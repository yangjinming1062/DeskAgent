import json
from urllib.parse import unquote

from utils import (
    SECRET_PREFIX_RE,
    check_website_access,
    is_always_blocked_url,
    normalize_url_for_request,
)

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import (
    BROWSER_TAB_CLOSE_SCHEMA,
    BROWSER_TAB_LIST_SCHEMA,
    BROWSER_TAB_NEW_SCHEMA,
    BROWSER_TAB_SWITCH_SCHEMA,
)
from ._common import browser_session, camofox_unsupported, no_supervisor


def browser_tab_new(url: str | None = None, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_new")

    target_url = (url or "").strip()
    if target_url and target_url != "about:blank":
        if SECRET_PREFIX_RE.search(target_url) or SECRET_PREFIX_RE.search(unquote(target_url)):
            return json.dumps({"success": False, "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs."}, ensure_ascii=False)
        target_url = normalize_url_for_request(target_url)
        if is_always_blocked_url(target_url):
            return json.dumps({"success": False, "error": "Blocked: URL targets a cloud metadata endpoint"}, ensure_ascii=False)
        blocked = check_website_access(target_url)
        if blocked:
            return json.dumps({"success": False, "error": blocked.message}, ensure_ascii=False)
    else:
        target_url = "about:blank"

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        create_res = supervisor.send_cdp("Target.createTarget", {"url": target_url})
        if not create_res.get("ok"):
            return json.dumps({"success": False, "error": create_res.get("error", "Target.createTarget failed")})

        target_id = create_res["result"].get("targetId")
        if not target_id:
            return json.dumps({"success": False, "error": "No targetId returned from Target.createTarget"})

        attach_res = supervisor._attach_target(target_id)
        if not attach_res.get("ok"):
            return json.dumps({"success": False, "error": attach_res.get("error", "Failed to attach to new tab")})

        session_id = attach_res["result"].get("sessionId")
        if session_id:
            supervisor.set_active_session_id(session_id)

        return json.dumps({"success": True, "tab_id": target_id, "url": target_url})


def browser_tab_switch(tab_id: str, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_switch")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        _, attached = supervisor.get_attached_targets()
        session_id = attached.get(tab_id, {}).get("session_id")

        if not session_id:
            attach_res = supervisor._attach_target(tab_id)
            if not attach_res.get("ok"):
                return json.dumps({"success": False, "error": f"Failed to attach to tab {tab_id}: {attach_res.get('error')}"})
            session_id = attach_res["result"].get("sessionId")

        supervisor.set_active_session_id(session_id)
        return json.dumps({"success": True, "active_tab_id": tab_id})


def browser_tab_close(tab_id: str | None = None, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_close")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.close_tab(tab_id)
        if res.get("ok"):
            return json.dumps({"success": True, "closed_tab_id": res.get("tab_id")})
        return json.dumps({"success": False, "error": res.get("error", "Failed to close tab")})


def browser_tab_list(task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_list")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.list_tabs()
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "Target.getTargets failed")})

        targets = res["result"].get("targetInfos", [])
        active_sid, attached = supervisor.get_attached_targets()

        tabs = []
        for t in targets:
            if t.get("type") == "page":
                tid = t.get("targetId")
                sid = attached.get(tid, {}).get("session_id")
                is_active = (sid == active_sid) if active_sid else False
                tabs.append(
                    {
                        "tab_id": tid,
                        "url": t.get("url", ""),
                        "title": t.get("title", ""),
                        "is_active": is_active,
                    },
                )

        return json.dumps({"success": True, "tabs": tabs, "count": len(tabs)})


registry.register_tool("browser_tab_new", check_fn=check_browser_native_requirements, schema=BROWSER_TAB_NEW_SCHEMA)(
    lambda args, **kw: browser_tab_new(url=args.get("url"), task_id=kw.get("task_id")),
)

registry.register_tool("browser_tab_switch", check_fn=check_browser_native_requirements, schema=BROWSER_TAB_SWITCH_SCHEMA)(
    lambda args, **kw: browser_tab_switch(tab_id=args.get("tab_id", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_tab_close", check_fn=check_browser_native_requirements, schema=BROWSER_TAB_CLOSE_SCHEMA)(
    lambda args, **kw: browser_tab_close(tab_id=args.get("tab_id"), task_id=kw.get("task_id")),
)

registry.register_tool("browser_tab_list", check_fn=check_browser_native_requirements, schema=BROWSER_TAB_LIST_SCHEMA)(
    lambda args, **kw: browser_tab_list(task_id=kw.get("task_id")),  # noqa: ARG005
)
