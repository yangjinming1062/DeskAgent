import json
import logging
from typing import Any

from ..registry import registry
from .browser_supervisor import SUPERVISOR_REGISTRY

logger = logging.getLogger(__name__)

BROWSER_DIALOG_SCHEMA: dict[str, Any] = {
    "name": "browser_dialog",
    "description": (
        "Respond to a native JavaScript dialog (alert / confirm / prompt / "
        "beforeunload) that is currently blocking the page.\n\n"
        "**Workflow:** call ``browser_snapshot`` first — if a dialog is open, "
        "it appears in the ``pending_dialogs`` field with ``id``, ``type``, "
        "and ``message``. Then call this tool with ``action='accept'`` or "
        "``action='dismiss'``.\n\n"
        "**Prompt dialogs:** pass ``prompt_text`` to supply the response "
        "string. Ignored for alert/confirm/beforeunload.\n\n"
        "**Multiple dialogs:** if more than one dialog is queued (rare — "
        "happens when a second dialog fires while the first is still open), "
        "pass ``dialog_id`` from the snapshot to disambiguate.\n\n"
        "**Availability:** only present when a CDP-capable backend is "
        "attached — local Chromium-family browser via ``/browser connect``, "
        "or ``browser.cdp_url`` in config.yaml. "
        "Not available on Camofox (REST-only) or the default Playwright "
        "local browser (CDP port is hidden)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["accept", "dismiss"],
                "description": (
                    "'accept' clicks OK / returns the prompt text. "
                    "'dismiss' clicks Cancel / returns null from prompt(). "
                    "For ``beforeunload`` dialogs: 'accept' allows the "
                    "navigation, 'dismiss' keeps the page."
                ),
            },
            "prompt_text": {
                "type": "string",
                "description": ("Response string for a ``prompt()`` dialog. Ignored for " "other dialog types. Defaults to empty string."),
            },
            "dialog_id": {
                "type": "string",
                "description": ("Specific dialog to respond to, from " "``browser_snapshot.pending_dialogs[].id``. Required " "only when multiple dialogs are queued."),
            },
        },
        "required": ["action"],
    },
}


def browser_dialog(
    action: str,
    prompt_text: str | None = None,
    dialog_id: str | None = None,
    task_id: str | None = None,
) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id or "default")) is None:
        return json.dumps({"success": False, "error": "No CDP supervisor is attached to this task. Call browser_navigate or /browser connect first."})

    res = supervisor.respond_to_dialog(action=action, prompt_text=prompt_text, dialog_id=dialog_id)
    return json.dumps({"success": True, "action": action, "dialog": res.get("dialog", {})} if res.get("ok") else {"success": False, "error": res.get("error", "unknown error")})


registry.register_tool("browser_dialog", schema=BROWSER_DIALOG_SCHEMA)(
    lambda args, **kw: browser_dialog(
        action=args.get("action", ""),
        prompt_text=args.get("prompt_text"),
        dialog_id=args.get("dialog_id"),
        task_id=kw.get("task_id"),
    )
)
