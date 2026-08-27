import asyncio
import contextlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from utils import get_spiritagent_dir, is_interrupted

from ..registry import registry, tool_error
from .helpers import (
    _detect_image_mime_type,
    _download_image,
    _validate_image_url_async,
    capped_image_data_url,
)

logger = logging.getLogger(__name__)

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": ("Load an image into the conversation so you can see it. Accepts an image URL (http/https) or a local file path."),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {"type": "string", "description": "Image URL (http/https) or local file path to load."},
        },
        "required": ["image_url"],
    },
}


async def vision_analyze_tool(image_url: str) -> dict[str, Any] | str:
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

        def _prepare_image() -> str:
            if not (mime := _detect_image_mime_type(temp_path)):
                raise ValueError("Only real image files are supported for vision analysis.")
            return capped_image_data_url(temp_path, mime)

        img_url = await asyncio.to_thread(_prepare_image)
        size = temp_path.stat().st_size
        return {
            "_multimodal": True,
            "content": [
                {"type": "text", "text": f"Image loaded ({size:,} bytes) from {image_url}. Inspect it and answer any pending question about it."},
                {"type": "image_url", "image_url": {"url": img_url}},
            ],
            "meta": {"source": image_url, "image_size_bytes": size},
        }
    except Exception as e:
        err_msg = f"Error loading image: {e}"
        logger.error("%s", err_msg, exc_info=True)
        return tool_error(err_msg, success=False)
    finally:
        if should_cleanup and temp_path:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(temp_path.unlink)


async def _handle_vision_analyze(args: dict[str, Any], **kw: Any) -> dict[str, Any] | str:
    return await vision_analyze_tool(args.get("image_url", ""))


registry.register_tool("vision_analyze", schema=VISION_ANALYZE_SCHEMA)(_handle_vision_analyze)
