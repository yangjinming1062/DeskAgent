import asyncio
import base64
import io as _io
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from utils import async_is_safe_url, cfg_get, check_website_access, load_config

logger = logging.getLogger(__name__)

_VISION_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_MAX_BASE64_BYTES = 20 * 1024 * 1024
_RESIZE_TARGET_BYTES = 5 * 1024 * 1024


def _resolve_download_timeout() -> float:
    try:
        if (v := cfg_get(load_config(), "auxiliary", "vision", "download_timeout")) is not None:
            return float(v)
    except Exception:
        pass
    return 30.0


def _resolve_vision_params(default_timeout: float = 120.0, default_temperature: float = 0.1) -> tuple[float, float]:
    """Read ``auxiliary.vision.timeout`` and ``auxiliary.vision.temperature``.

    Falls back to ``(default_timeout, default_temperature)`` on any error or
    missing key. Callers that need a higher floor (e.g. local models) should
    clamp ``timeout`` to their minimum after the call.
    """
    try:
        vc = cfg_get(load_config(), "auxiliary", "vision", default={})
        return float(vc.get("timeout", default_timeout)), float(vc.get("temperature", default_temperature))
    except Exception:
        return default_timeout, default_temperature


async def _validate_image_url_async(url: str) -> bool:
    return bool(url and isinstance(url, str) and url.startswith(("http://", "https://")) and urlparse(url).netloc) and await async_is_safe_url(url)


def _detect_image_mime_type(image_path: Path) -> str | None:
    with image_path.open("rb") as f:
        h = f.read(64)
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if h.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if h.startswith(b"BM"):
        return "image/bmp"
    if len(h) >= 12 and h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return "image/webp"
    return "image/svg+xml" if image_path.suffix.lower() == ".svg" and "<svg" in image_path.read_text(encoding="utf-8", errors="ignore")[:4096].lower() else None


_PAYMENT_HINTS = ("402", "insufficient", "payment required", "credits", "billing")
_SIZE_HINTS = ("too large", "payload", "413", "content_too_large", "request_too_large", "exceeds", "size limit")
_UNSUPPORT_HINTS = ("does not support", "not support image", "content_policy", "multimodal", "unrecognized request argument", "image input")


def _classify_api_error(error: Exception, media_label: str) -> str:
    err_str = str(error).lower()
    if any(h in err_str for h in _PAYMENT_HINTS):
        return f"Insufficient credits or payment required. Please top up your API provider account and try again. Error: {error}"
    if any(h in err_str for h in _UNSUPPORT_HINTS):
        return f"The model does not support {media_label} analysis or the request was rejected. Error: {error}"
    if any(h in err_str for h in _SIZE_HINTS):
        return f"The {media_label} is too large for the API. Error: {error}"
    if "invalid_request" in err_str or "image_url" in err_str:
        return f"The vision API rejected the image. Try a smaller JPEG/PNG and retry. Error: {error}"
    return f"There was a problem with the request and the {media_label} could not be analyzed. Error: {error}"


def _is_retryable_download_error(error: Exception) -> bool:
    if isinstance(error, (PermissionError, ValueError)):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status is None:
            return False
        return status == 429 or status >= 500
    # httpx transport errors (ConnectError, RemoteProtocolError, ReadTimeout,
    # etc.) — these are transient and worth retrying.
    if isinstance(error, (httpx.TransportError, httpx.TimeoutException, ConnectionError, OSError)):
        return True
    return False


async def _download_media(
    url: str,
    destination: Path,
    *,
    accept: str,
    max_bytes: int,
    timeout: float,
    media_label: str,
    max_retries: int = 3,
) -> Path:
    """Download a media file to ``destination`` with size cap, redirect
    safety, and retryable-error handling. The vision tool is the sole
    caller today; ``accept`` / ``max_bytes`` / ``timeout`` / ``media_label``
    remain parameterised so a future media tool can reuse the helper
    without copying the size-cap / redirect-guard machinery.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)

    async def _guard(response) -> None:
        if response.is_redirect and response.next_request and not await async_is_safe_url(redirect := str(response.next_request.url)):
            raise ValueError(f"Blocked redirect to private/internal address: {redirect}")

    last_err = None
    for attempt in range(max_retries):
        try:
            if blocked := check_website_access(url):
                raise PermissionError(blocked["message"])
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, event_hooks={"response": [_guard]}) as client:
                res = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": accept,
                    },
                )
                res.raise_for_status()
                if ((cl := res.headers.get("content-length")) and int(cl) > max_bytes) or len(body := res.content) > max_bytes:
                    raise ValueError(f"{media_label.capitalize()} too large")
                if blocked := check_website_access(str(res.url)):
                    raise PermissionError(blocked["message"])
                destination.write_bytes(body)
            return destination
        except Exception as e:
            last_err = e
            if not _is_retryable_download_error(e) or attempt >= max_retries - 1:
                logger.error(
                    "%s download failed after %s attempt(s): %s",
                    media_label.capitalize(),
                    attempt + 1,
                    str(e)[:100],
                    exc_info=True,
                )
                raise
            wait = 2 ** (attempt + 1)
            logger.warning(
                "%s download failed (attempt %s/%s): %s. Retrying in %ss...",
                media_label.capitalize(),
                attempt + 1,
                max_retries,
                str(e)[:50],
                wait,
            )
            await asyncio.sleep(wait)
    raise last_err or RuntimeError("No attempts made")


async def _download_image(image_url: str, destination: Path, max_retries: int = 3) -> Path:
    return await _download_media(
        image_url,
        destination,
        accept="image/*,*/*;q=0.8",
        max_bytes=_VISION_MAX_DOWNLOAD_BYTES,
        timeout=_resolve_download_timeout(),
        media_label="image",
        max_retries=max_retries,
    )


def _guess_mime_from_extension(image_path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp", ".svg": "image/svg+xml"}.get(
        image_path.suffix.lower(), "image/jpeg"
    )


def _file_to_base64_data_url(file_path: Path, mime_type: str | None = None, default_mime: str = "application/octet-stream") -> str:
    mime = mime_type or default_mime
    return f"data:{mime};base64,{base64.b64encode(file_path.read_bytes()).decode('ascii')}"


def _image_to_base64_data_url(image_path: Path, mime_type: str | None = None) -> str:
    return _file_to_base64_data_url(image_path, mime_type=mime_type or _guess_mime_from_extension(image_path))


def _is_image_size_error(error: Exception) -> bool:
    err_str = str(error).lower()
    return any(h in err_str for h in _SIZE_HINTS) or "image_url" in err_str or "invalid_request" in err_str


def _resize_image_for_vision(image_path: Path, mime_type: str | None = None, max_base64_bytes: int = _RESIZE_TARGET_BYTES, max_dimension: int | None = None) -> str:
    file_size = image_path.stat().st_size
    estimated_b64 = (file_size * 4) // 3 + 100
    needs_resize = estimated_b64 > max_base64_bytes

    # Single Image.open() — reuse for both dimension check and resize.
    img = None
    if not needs_resize or max_dimension is not None:
        try:
            img = Image.open(image_path)
            if max_dimension is not None and max(img.size) > max_dimension:
                needs_resize = True
        except Exception:
            pass

    if not needs_resize:
        if img is not None:
            img.close()
        data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        if len(data_url) <= max_base64_bytes:
            return data_url
        # Re-open for resize since base64 exceeded despite file size estimate.
        try:
            img = Image.open(image_path)
        except Exception as exc:
            logger.info("Pillow cannot open image for resizing: %s", exc)
            return data_url

    if img is None:
        try:
            img = Image.open(image_path)
        except Exception as exc:
            logger.info("Pillow cannot open image for resizing: %s", exc)
            return _image_to_base64_data_url(image_path, mime_type=mime_type)

    mime = mime_type or _guess_mime_from_extension(image_path)
    pil_format = "PNG" if mime == "image/png" else "JPEG"
    out_mime = "image/png" if pil_format == "PNG" else "image/jpeg"

    if pil_format == "JPEG" and img.mode in {"RGBA", "P"}:
        img = img.convert("RGB")

    quality_steps = (85, 70, 50) if pil_format == "JPEG" else (None,)
    prev_dims = (img.width, img.height)
    # Track the smallest candidate seen so we can return the best effort
    # when no iteration actually meets `max_base64_bytes` — the previous
    # fallback returned the ORIGINAL-size base64, which is the opposite of
    # what the caller wants when resizing failed to fit.
    best_candidate: str | None = None

    for attempt in range(5):
        if attempt > 0:
            new_w, new_h = max(int(img.width * 0.5), 64), max(int(img.height * 0.5), 64)
            if new_w == 64 and img.width > 0:
                new_h = max(int(img.height * (64 / img.width)), 64)
            elif new_h == 64 and img.height > 0:
                new_w = max(int(img.width * (64 / img.height)), 64)
            if (new_w, new_h) == prev_dims:
                break
            img = img.resize((new_w, new_h), Image.LANCZOS)
            prev_dims = (new_w, new_h)

        for q in quality_steps:
            buf = _io.BytesIO()
            img.save(buf, format=pil_format, **({"quality": q} if q is not None else {}))
            candidate = f"data:{out_mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
            if best_candidate is None or len(candidate) < len(best_candidate):
                best_candidate = candidate
            if len(candidate) <= max_base64_bytes and (max_dimension is None or max(img.width, img.height) <= max_dimension):
                return candidate
    # No iteration fit. Return the smallest candidate we produced, NOT
    # the original (the caller has already decided the original is too
    # big — returning it would defeat the whole resize chain).
    return best_candidate or _image_to_base64_data_url(image_path, mime_type=mime_type)
