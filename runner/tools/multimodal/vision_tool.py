import contextlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from utils import call_llm, clean_output, get_spiritagent_dir

from ..debug_helpers import DebugSession
from ..interrupt import is_interrupted
from ..registry import registry, tool_error
from .helpers import (
    _MAX_BASE64_BYTES,
    _classify_api_error,
    _detect_image_mime_type,
    _download_image,
    _image_to_base64_data_url,
    _validate_image_url_async,
    is_image_size_error,
    resize_image_for_vision,
    resolve_vision_params,
)

logger = logging.getLogger(__name__)
_debug = DebugSession("vision_tools", env_var="VISION_TOOLS_DEBUG")

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": ("Load an image into the conversation so you can see it. Accepts a URL, local file path, or data URL."),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {"type": "string", "description": "Image URL (http/https), local file path, or data: URL to load."},
            "question": {"type": "string", "description": "Your specific question or request about the image."},
        },
        "required": ["image_url", "question"],
    },
}


async def vision_analyze_tool(image_url: str, user_prompt: str) -> str:
    user_prompt = str(user_prompt or "")
    debug_call_data = {
        "parameters": {"image_url": image_url, "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt},
        "error": None,
        "success": False,
        "analysis_length": 0,
        "image_size_bytes": 0,
    }
    temp_path, should_cleanup = None, True
    try:
        if is_interrupted():
            return tool_error("Interrupted", success=False)
        resolved = image_url.removeprefix("file://")
        local_path = Path(os.path.expanduser(resolved))
        if local_path.is_file():
            temp_path, should_cleanup = local_path, False
        elif await _validate_image_url_async(image_url):
            temp_path = get_spiritagent_dir("cache/vision", "temp_vision_images") / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_path)
        else:
            raise ValueError("Invalid image source. Provide an HTTP/HTTPS URL or a valid local file path.")

        debug_call_data["image_size_bytes"] = temp_path.stat().st_size
        if not (mime := _detect_image_mime_type(temp_path)):
            raise ValueError("Only real image files are supported for vision analysis.")

        # Quick size check before reading the entire file into memory.
        if temp_path.stat().st_size > _MAX_BASE64_BYTES * 3 // 4:
            img_url = resize_image_for_vision(temp_path, mime_type=mime)
        else:
            img_url = _image_to_base64_data_url(temp_path, mime_type=mime)
        if len(img_url) > _MAX_BASE64_BYTES:
            img_url = resize_image_for_vision(temp_path, mime_type=mime)
            if len(img_url) > _MAX_BASE64_BYTES:
                raise ValueError("Image too large for vision API even after resizing.")

        messages = [{"role": "user", "content": [{"type": "text", "text": user_prompt}, {"type": "image_url", "image_url": {"url": img_url}}]}]
        timeout, temp = resolve_vision_params()

        call_kwargs = {"task": "vision", "messages": messages, "temperature": temp, "max_tokens": 2000, "timeout": timeout}

        try:
            res = await call_llm(**call_kwargs)
        except Exception as api_err:
            if not is_image_size_error(api_err):
                raise
            # Provider rejected the image as too large. Resize and retry
            # exactly once. The retry uses RESIZE_TARGET_BYTES (5 MB) as
            # the ceiling, which is well below _MAX_BASE64_BYTES (20 MB),
            # so a second resize-on-error is unnecessary.
            img_url = resize_image_for_vision(temp_path, mime_type=mime)
            messages[0]["content"][1]["image_url"]["url"] = img_url
            res = await call_llm(**call_kwargs)

        analysis = clean_output(res) or "There was a problem with the request and the image could not be analyzed."

        result = {"success": True, "analysis": analysis}
        debug_call_data |= {"success": True, "analysis_length": len(analysis)}
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        err_msg = f"Error analyzing image: {e}"
        logger.error("%s", err_msg, exc_info=True)
        analysis = _classify_api_error(e, "image")
        debug_call_data["error"] = err_msg

        # Tool-exit boundary: the LLM-facing envelope plus the classified
        # fallback analysis are the error contract callers parse.
        return json.dumps({"success": False, "error": err_msg, "analysis": analysis}, indent=2, ensure_ascii=False)
    finally:
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()
        if should_cleanup and temp_path:
            with contextlib.suppress(Exception):
                temp_path.unlink()


def _handle_vision_analyze(args: dict[str, Any], **kw: Any) -> Awaitable[str]:
    url, q = args.get("image_url", ""), args.get("question", "")
    return vision_analyze_tool(url, f"Fully describe and explain everything about this image, then answer the following question:\n\n{q}")


registry.register_tool("vision_analyze", schema=VISION_ANALYZE_SCHEMA)(_handle_vision_analyze)
