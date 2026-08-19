import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx
from components import SETTINGS, download_capped, get_logger, log_paid_call

logger = get_logger(__name__)

DEFAULT_BASE_URL: str = "https://openapi.tripo3d.ai/v3"

MODEL_VERSION_DEFAULT: str = "v3.1-20260211"
MODEL_VERSION_MIXAMO: str = "v1.0-20240301"
MODEL_VERSION_TRIPO: str = "v2.5-20260210"

_RIG_MODEL_VERSIONS: dict[str, str] = {"mixamo": MODEL_VERSION_MIXAMO, "tripo": MODEL_VERSION_TRIPO}

_RIG_SPECS: dict[str, str] = {"biped": "mixamo", "quadruped": "tripo", "avian": "tripo", "serpentine": "tripo", "aquatic": "tripo", "hexapod": "tripo", "octopod": "tripo"}

_DOWNLOAD_TIMEOUT_SECONDS: float = 120.0

# P-series models are low-poly-optimised and cap ``face_limit``; clamping protects operators who toggle ``tripo_model_version`` to a P-series id.
_P_SERIES_FACE_LIMIT_MAX: int = 20_000


class TripoApiError(RuntimeError):
    """Non-zero ``code`` in the v3 response envelope."""


class TripoTaskFailed(RuntimeError):
    """Polled task reached a terminal non-success status."""


def _base_url() -> str:
    return SETTINGS.tripo_base_url or DEFAULT_BASE_URL


def _api_key() -> str:
    key = getattr(SETTINGS, "tripo_api_key", "") or ""
    if not key:
        raise TripoApiError("TRIPO_API_KEY is not configured")
    return key


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _envelope(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("code")
    status = payload.get("status")
    if code != 0 or status != "success":
        raise TripoApiError(f"tripo code={code} status={status}: {payload.get('message')}")
    return payload.get("data") or {}


def _common_model_kwargs(
    *,
    model_version: str,
    pbr: bool,
    texture_quality: str | None,
    face_limit: int | None,
    enable_autofix: bool | None,
    texture_alignment: str | None = None,
    orientation: str | None = None,
) -> dict[str, Any]:
    """Optional Tripo3D payload fields shared by the H- and M-series endpoints.

    ``texture_alignment`` and ``orientation`` are multiview-only framing hints —
    omitted from the payload when ``None`` so the single-image endpoint does not
    receive fields its schema does not declare.
    """
    payload: dict[str, Any] = {"model": model_version, "pbr": pbr}
    if texture_quality:
        payload["texture_quality"] = texture_quality
    if face_limit is not None:
        payload["face_limit"] = min(face_limit, _P_SERIES_FACE_LIMIT_MAX) if model_version.startswith("P") else face_limit
    if enable_autofix is not None:
        payload["enable_image_autofix"] = enable_autofix
    if model_version.startswith("v3") and SETTINGS.tripo_geometry_quality:
        payload["geometry_quality"] = SETTINGS.tripo_geometry_quality
    if texture_alignment is not None:
        payload["texture_alignment"] = texture_alignment
    if orientation is not None:
        payload["orientation"] = orientation
    return payload


def tripo_common_kwargs_from_settings(*, model_version: str | None = None, texture_alignment: str | None = None, orientation: str | None = None) -> dict[str, Any]:
    """Build common call kwargs for Tripo endpoints from SETTINGS."""
    kwargs: dict[str, Any] = {
        "model_version": model_version if model_version is not None else SETTINGS.tripo_model_version,
        "pbr": True,
        "texture_quality": SETTINGS.tripo_texture_quality,
        "face_limit": SETTINGS.tripo_face_limit or None,
        "enable_autofix": SETTINGS.tripo_enable_autofix,
    }
    if texture_alignment is not None:
        kwargs["texture_alignment"] = texture_alignment
    if orientation is not None:
        kwargs["orientation"] = orientation
    return kwargs


async def upload_file(file_bytes: bytes, filename: str, content_type: str = "image/jpeg") -> str:
    """POST /v3/files — multipart upload, returns a ``file_token`` for use as ``input`` in image-to-model."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{_base_url()}/files", headers={"Authorization": f"Bearer {_api_key()}"}, files={"file": (filename, file_bytes, content_type)})
    return _envelope(resp.json())["file_token"]


async def create_multiview_to_model(
    views: dict[str, str],
    *,
    model_version: str = MODEL_VERSION_DEFAULT,
    pbr: bool = True,
    texture_quality: str | None = None,
    face_limit: int | None = None,
    enable_autofix: bool | None = None,
    texture_alignment: str = "original_image",
    orientation: str = "align_image",
) -> str:
    """views maps perspective keys ∈ {front, left, back, right} to file_token or public URL.

    'front' is required; at least 2 views must be provided.
    """
    if not views.get("front"):
        raise ValueError("multiview-to-model requires a 'front' view")
    if len(views) < 2:
        raise ValueError("multiview-to-model requires at least 2 views")
    inputs = [{view: views[view]} for view in ("front", "right", "back", "left") if views.get(view)]
    payload = _common_model_kwargs(
        model_version=model_version,
        pbr=pbr,
        texture_quality=texture_quality,
        face_limit=face_limit,
        enable_autofix=enable_autofix,
        texture_alignment=texture_alignment,
        orientation=orientation,
    )
    payload["inputs"] = inputs
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{_base_url()}/generation/multiview-to-model", headers=_auth_headers(), json=payload)
    task_id = _envelope(resp.json())["task_id"]
    log_paid_call("tripo", "image_to_3d_submit", task_id=task_id)
    return task_id


async def create_image_to_model(
    image_token: str,
    *,
    model_version: str = MODEL_VERSION_DEFAULT,
    pbr: bool = True,
    texture_quality: str | None = None,
    face_limit: int | None = None,
    enable_autofix: bool | None = None,
) -> str:
    """Single-image to 3D model (H系列 ``image-to-model`` endpoint)."""
    if not image_token:
        raise ValueError("image-to-model requires a non-empty image_token")
    payload = _common_model_kwargs(model_version=model_version, pbr=pbr, texture_quality=texture_quality, face_limit=face_limit, enable_autofix=enable_autofix)
    payload["input"] = image_token
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{_base_url()}/generation/image-to-model", headers=_auth_headers(), json=payload)
    task_id = _envelope(resp.json())["task_id"]
    log_paid_call("tripo", "image_to_3d_submit", task_id=task_id)
    return task_id


async def get_task(task_id: str) -> dict[str, Any]:
    """Single GET /v3/tasks/{id}; status mapping is the caller's job."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(f"{_base_url()}/tasks/{task_id}", headers={"Authorization": f"Bearer {_api_key()}"})
    data = _envelope(resp.json())
    if data.get("status") == "success":
        log_paid_call("tripo", "image_to_3d_result", task_id=task_id, urls=[(data.get("output") or {}).get("model_url")], level="debug")
    return data


async def poll_task(task_id: str, *, interval: float = 5.0, timeout: float = 1800.0, on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Polls until terminal status; returns the final ``data`` payload (with ``output.model_url`` on success)."""
    deadline = time.monotonic() + timeout
    while True:
        data = await get_task(task_id)
        status = data.get("status")
        if on_progress is not None:
            on_progress(data)
        if status == "success":
            return data
        if status in ("failed", "cancelled", "banned"):
            raise TripoTaskFailed(f"task {task_id} reached status={status}: {data.get('message') or data}")
        if time.monotonic() > deadline:
            raise TripoTaskFailed(f"task {task_id} did not finish within {timeout}s")
        await asyncio.sleep(interval)


async def account_balance() -> dict[str, float]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{_base_url()}/account/balance", headers=_auth_headers())
    return _envelope(resp.json())


async def rig_check(task_id: str) -> str:
    """Starts an ``animate_prerigcheck`` task. Returns the task_id; poll it to read ``output.rig_type`` and ``output.riggable``."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{_base_url()}/animations/rig-check", headers=_auth_headers(), json={"input": task_id})
    return _envelope(resp.json())["task_id"]


async def poll_rig_check(task_id: str, *, interval: float = 2.0, timeout: float = 60.0) -> dict[str, Any]:
    """Polls an ``animate_prerigcheck`` task until terminal status; returns its ``output`` dict."""
    deadline = time.monotonic() + timeout
    while True:
        data = await get_task(task_id)
        if data.get("status") == "success":
            return data.get("output") or {}
        if data.get("status") in ("failed", "cancelled", "banned"):
            raise TripoTaskFailed(f"task {task_id} reached status={data.get('status')}: {data.get('message') or data}")
        if time.monotonic() > deadline:
            raise TripoTaskFailed(f"task {task_id} did not finish within {timeout}s")
        await asyncio.sleep(interval)


def rig_spec(rig_type: str) -> str:
    return _RIG_SPECS.get(rig_type, "tripo")


def rig_model_version(rig_type: str) -> str:
    return _RIG_MODEL_VERSIONS.get(rig_spec(rig_type), MODEL_VERSION_TRIPO)


async def rig(task_id: str, rig_type: str, *, spec: str | None = None, model_version: str | None = None) -> str:
    """Bones the model produced by ``task_id``. Returns the new rigged task_id."""
    chosen_spec = spec or rig_spec(rig_type)
    chosen_version = model_version or rig_model_version(rig_type)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_base_url()}/animations/rig", headers=_auth_headers(), json={"input": task_id, "rig_type": rig_type, "spec": chosen_spec, "model": chosen_version}
        )
    return _envelope(resp.json())["task_id"]


async def download_model(model_url: str) -> bytes:
    """Tripo model URLs are short-lived; download immediately after the rig task succeeds."""
    return await download_capped(model_url, max_bytes=100 * 1024 * 1024, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
