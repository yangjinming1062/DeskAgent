import base64
import json
import logging
import uuid
from pathlib import Path

from utils import call_llm_sync, get_spiritagent_home, redact_sensitive_text

from ...multimodal import resolve_vision_params
from ...registry import registry
from ..camofox import camofox_vision, is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_VISION_SCHEMA
from ._common import browser_session, no_supervisor

logger = logging.getLogger(__name__)


def browser_vision(question: str, annotate: bool = False, task_id: str | None = None) -> str:
    """截图当前页面并通过多模态 LLM 进行分析。"""
    if is_camofox_mode():
        return camofox_vision(question, annotate=annotate, task_id=task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        screenshots_dir = get_spiritagent_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")

        shot_res = supervisor.screenshot(path=screenshot_path)
        if not shot_res.get("ok"):
            return json.dumps({"success": False, "error": shot_res.get("error", "Failed to capture screenshot")})

        annotation_context = ""
        if annotate:
            try:
                snap_res = supervisor.snapshot_axtree(interactive_only=True)
                if snap_res.get("ok"):
                    annotation_context = f"\n\nAccessibility tree (element refs for interaction):\n{snap_res.get('snapshot', '')[:3000]}"
            except Exception as exc:
                logger.debug("Failed to obtain snapshot for vision annotation: %s", exc)

        annotation_context = redact_sensitive_text(annotation_context)
        vision_prompt = f"Analyze this browser screenshot and answer: {question}{annotation_context}"

        try:
            vision_timeout, vision_temperature = resolve_vision_params()
        except Exception:
            vision_timeout, vision_temperature = 120.0, 0.1

        try:
            raw_bytes = Path(screenshot_path).read_bytes()
            img_b64 = base64.b64encode(raw_bytes).decode("utf-8")
            response = call_llm_sync(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        ],
                    },
                ],
                task="vision",
                temperature=vision_temperature,
                timeout=vision_timeout,
            )
            analysis = redact_sensitive_text((response or "").strip())
            return json.dumps({"success": True, "analysis": analysis, "screenshot_path": screenshot_path}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc), "screenshot_path": screenshot_path}, ensure_ascii=False)


registry.register_tool("browser_vision", check_fn=check_browser_native_requirements, schema=BROWSER_VISION_SCHEMA)(
    lambda args, **kw: browser_vision(question=args.get("question", ""), annotate=args.get("annotate", False), task_id=kw.get("task_id")),
)
