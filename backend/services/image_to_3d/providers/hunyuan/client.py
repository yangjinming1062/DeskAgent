from typing import Any

import httpx
from components import SETTINGS, download_capped, get_logger, log_paid_call

logger = get_logger(__name__)

DEFAULT_BASE_URL: str = "https://tokenhub.tencentmaas.com"

MODEL_VERSION_DEFAULT: str = "hy-3d-3.1"
MODEL_VERSION_3_0: str = "hy-3d-3.0"
MODEL_VERSION_EXPRESS: str = "hy-3d-express"

_DOWNLOAD_TIMEOUT_SECONDS: float = 120.0
_DOWNLOAD_MAX_BYTES: int = 100 * 1024 * 1024

# Face count bounds per Tencent Hunyuan 3D specs
_MIN_FACE_COUNT: int = 3_000
_MAX_FACE_COUNT: int = 1_500_000


class HunyuanApiError(RuntimeError):
    """API error returned by Hunyuan 3D service."""


def _base_url() -> str:
    url = getattr(SETTINGS, "hunyuan_base_url", "") or DEFAULT_BASE_URL
    return url.rstrip("/")


def _api_key() -> str:
    key = getattr(SETTINGS, "hunyuan_api_key", "") or ""
    if not key:
        raise HunyuanApiError("HUNYUAN_API_KEY is not configured")
    return key


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _common_model_kwargs(
    *, model: str, enable_pbr: bool = True, result_format: str = "GLB", generate_type: str | None = None, face_count: int | None = None, prompt: str | None = None
) -> dict[str, Any]:
    """Build common payload fields for Hunyuan 3D API in snake_case."""
    payload: dict[str, Any] = {"model": model, "enable_pbr": enable_pbr, "result_format": (result_format or "GLB").upper()}
    if generate_type:
        payload["generate_type"] = generate_type
    if face_count is not None and face_count > 0:
        payload["face_count"] = max(_MIN_FACE_COUNT, min(face_count, _MAX_FACE_COUNT))
    if prompt:
        payload["prompt"] = prompt
    return payload


def hunyuan_common_kwargs_from_settings(
    *, model: str | None = None, generate_type: str | None = None, face_count: int | None = None, enable_pbr: bool | None = None, result_format: str | None = None
) -> dict[str, Any]:
    """Build common call kwargs for Hunyuan endpoints from SETTINGS."""
    raw_face_count = face_count if face_count is not None else getattr(SETTINGS, "hunyuan_face_count", 0)
    fc = raw_face_count if (raw_face_count is not None and raw_face_count > 0) else None
    rf = result_format if result_format is not None else (getattr(SETTINGS, "hunyuan_result_format", "") or "GLB")
    return {
        "model": model if model is not None else (getattr(SETTINGS, "hunyuan_model_version", "") or MODEL_VERSION_DEFAULT),
        "generate_type": generate_type if generate_type is not None else getattr(SETTINGS, "hunyuan_generate_type", None),
        "face_count": fc,
        "enable_pbr": enable_pbr if enable_pbr is not None else getattr(SETTINGS, "hunyuan_enable_pbr", True),
        "result_format": rf.upper() if rf else "GLB",
    }


async def _submit_job(payload: dict[str, Any], *, paid_label: str) -> str:
    """POST /v1/api/3d/submit shared by every submission mode."""
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_base_url()}/v1/api/3d/submit", headers=_auth_headers(), json=payload)
    if resp.status_code != 200:
        raise HunyuanApiError(f"hunyuan submit HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    job_id = str(body.get("id") or "")
    if not job_id:
        raise HunyuanApiError(f"hunyuan submit response missing job id: {str(body)[:300]}")
    log_paid_call("hunyuan", paid_label, task_id=job_id)
    return job_id


async def create_image_to_model(
    image_base64: str,
    *,
    model: str = MODEL_VERSION_DEFAULT,
    enable_pbr: bool = True,
    result_format: str = "GLB",
    generate_type: str | None = None,
    face_count: int | None = None,
    prompt: str | None = None,
) -> str:
    """Submit a single-image to 3D model generation job."""
    if not image_base64:
        raise ValueError("image-to-model requires a non-empty image_base64")
    payload = _common_model_kwargs(model=model, enable_pbr=enable_pbr, result_format=result_format, generate_type=generate_type, face_count=face_count, prompt=prompt)
    payload["image_base64"] = image_base64
    return await _submit_job(payload, paid_label="image_to_3d_submit")


async def create_multiview_to_model(
    front_image_base64: str,
    views: dict[str, str],
    *,
    model: str = MODEL_VERSION_DEFAULT,
    enable_pbr: bool = True,
    result_format: str = "GLB",
    generate_type: str | None = None,
    face_count: int | None = None,
    prompt: str | None = None,
) -> str:
    """Submit a multi-view to 3D model generation job.

    ``front_image_base64`` is the main front view (passed in ``image_base64``).
    ``views`` maps perspective keys (e.g. ``right``, ``back``, ``left``, ``top``, ``bottom``)
    to base64 strings or URLs, which are formatted into ``multi_view_images``.
    """
    if not front_image_base64:
        raise ValueError("multiview-to-model requires a front image")

    # ViewImage 字段名（view_type/view_image_base64）与主图的 image_base64 不对称，见
    # https://cloud.tencent.com/document/api/1804/120828#ViewImage
    multi_view_images = [{"view_type": view_name, "view_image_base64": b64_data} for view_name, b64_data in views.items() if view_name.lower() != "front" and b64_data]

    payload = _common_model_kwargs(model=model, enable_pbr=enable_pbr, result_format=result_format, generate_type=generate_type, face_count=face_count, prompt=prompt)
    payload["image_base64"] = front_image_base64
    if multi_view_images:
        payload["multi_view_images"] = multi_view_images
    return await _submit_job(payload, paid_label="image_to_3d_submit")


async def get_task(job_id: str, *, model: str = MODEL_VERSION_DEFAULT) -> dict[str, Any]:
    """Single POST /v1/api/3d/query to check task status."""
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_base_url()}/v1/api/3d/query", headers=_auth_headers(), json={"id": job_id, "model": model})
    if resp.status_code != 200:
        logger.warning("hunyuan query failed", extra={"task_id": job_id, "status_code": resp.status_code})
        raise HunyuanApiError(f"hunyuan query HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def download_model(model_url: str) -> bytes:
    """Download model asset with download_capped."""
    return await download_capped(model_url, max_bytes=_DOWNLOAD_MAX_BYTES, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
