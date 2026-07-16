import contextlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from utils import call_llm
from utils import get_zast_dir

from ..debug_helpers import DebugSession
from ..interrupt import is_interrupted
from ..registry import registry
from ..registry import tool_error
from .helpers import _classify_api_error
from .helpers import _download_media
from .helpers import _file_to_base64_data_url
from .helpers import _resolve_vision_params
from .helpers import _validate_image_url_async

logger = logging.getLogger(__name__)
_debug = DebugSession("video_tools", env_var="VIDEO_TOOLS_DEBUG")

VIDEO_ANALYZE_SCHEMA = {
    "name": "video_analyze",
    "description": ("Analyze a video from a URL or local file path using a multimodal AI model."),
    "parameters": {
        "type": "object",
        "properties": {
            "video_url": {"type": "string", "description": "Video URL (http/https) or local file path to analyze."},
            "question": {"type": "string", "description": "Your specific question about the video."},
        },
        "required": ["video_url", "question"],
    },
}

_VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/mov",
    ".avi": "video/mp4",
    ".mkv": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}
_MAX_VIDEO_BASE64_BYTES = 50 * 1024 * 1024
_VIDEO_SIZE_WARN_BYTES = 20 * 1024 * 1024


def _detect_video_mime_type(video_path: Path) -> str | None:
    return _VIDEO_MIME_TYPES.get(video_path.suffix.lower())


def _video_to_base64_data_url(video_path: Path, mime_type: str | None = None) -> str:
    return _file_to_base64_data_url(video_path, mime_type=mime_type or _detect_video_mime_type(video_path), default_mime="video/mp4")


async def _download_video(video_url: str, destination: Path, max_retries: int = 3) -> Path:
    return await _download_media(
        video_url,
        destination,
        accept="video/*,*/*;q=0.8",
        max_bytes=_MAX_VIDEO_BASE64_BYTES,
        timeout=60.0,
        media_label="video",
        max_retries=max_retries,
    )


async def video_analyze_tool(video_url: str, user_prompt: str) -> str:
    user_prompt = str(user_prompt or "")
    debug_call_data = {
        "parameters": {"video_url": video_url, "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt},
        "error": None,
        "success": False,
        "analysis_length": 0,
        "video_size_bytes": 0,
    }
    temp_path, should_cleanup = None, True
    try:
        if is_interrupted():
            return tool_error("Interrupted", success=False)
        resolved = video_url.removeprefix("file://")
        local_path = Path(os.path.expanduser(resolved))
        if local_path.is_file():
            temp_path, should_cleanup = local_path, False
        elif await _validate_image_url_async(video_url):
            temp_path = get_zast_dir("cache/video", "temp_video_files") / f"temp_video_{uuid.uuid4()}.mp4"
            await _download_video(video_url, temp_path)
        else:
            raise ValueError("Invalid video source. Provide an HTTP/HTTPS URL or a valid local file path.")

        debug_call_data["video_size_bytes"] = temp_path.stat().st_size
        if not (detected_mime := _detect_video_mime_type(temp_path)):
            raise ValueError(f"Unsupported video format: '{temp_path.suffix}'")

        video_data_url = _video_to_base64_data_url(temp_path, mime_type=detected_mime)
        if len(video_data_url) > _MAX_VIDEO_BASE64_BYTES:
            raise ValueError("Video too large for API.")

        messages = [{"role": "user", "content": [{"type": "text", "text": user_prompt}, {"type": "video_url", "video_url": {"url": video_data_url}}]}]
        # Local vision models serving video inference can easily exceed the
        # default 120s, so clamp the configured timeout to a 180s floor.
        timeout, temp = _resolve_vision_params(default_timeout=180.0)
        timeout = max(timeout, 180.0)

        call_kwargs = {"task": "vision", "messages": messages, "temperature": temp, "max_tokens": 4000, "timeout": timeout}
        res = await call_llm(**call_kwargs)
        from ..system import clean_output

        analysis = clean_output(res) or "There was a problem with the request and the video could not be analyzed."

        debug_call_data |= {"success": True, "analysis_length": len(analysis)}
        _debug.log_call("video_analyze_tool", debug_call_data)
        _debug.save()
        return json.dumps({"success": True, "analysis": analysis}, indent=2, ensure_ascii=False)
    except Exception as e:
        err_msg = f"Error analyzing video: {e}"
        logger.error("%s", err_msg, exc_info=True)
        analysis = _classify_api_error(e, "video")

        _debug.log_call("video_analyze_tool", debug_call_data | {"error": err_msg})
        _debug.save()
        return json.dumps({"success": False, "error": err_msg, "analysis": analysis}, indent=2, ensure_ascii=False)
    finally:
        if should_cleanup and temp_path:
            with contextlib.suppress(Exception):
                temp_path.unlink()


def _handle_video_analyze(args: dict[str, Any], **kw: Any) -> Awaitable[str]:
    url, q = args.get("video_url", ""), args.get("question", "")
    return video_analyze_tool(url, f"Fully describe and explain everything happening in this video, then answer the following question:\n\n{q}")


registry.register_tool("video_analyze", schema=VIDEO_ANALYZE_SCHEMA)(_handle_video_analyze)
