import asyncio
import base64
from datetime import timedelta
from typing import Any

from common import get_router
from components import get_file_path
from components import get_logger
from components import naive_utc_now
from components import save_file
from components import SESSION_LOCAL
from components import SETTINGS
from components import STT_MAX_AUDIO_BYTES
from components import TTS_MAX_TEXT_CHARS
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from services.llm import classify_api_error
from services.llm import execute_with_fallback
from services.llm import ImageGenRequest
from services.llm import MissingLlmConfigError
from services.llm import pick_voice_id
from services.llm import resolve_provider_chain
from services.media import enqueue_video_job
from services.media import get_job
from services.rate_limit import limiter

from ._http_errors import classified_http_exception
from ._http_errors import missing_config_http

logger = get_logger(__name__)

router = get_router()


# ── Helpers ────────────────────────────────────────────────────────────


def _llm_http_error(e: Exception, op: str) -> HTTPException:
    """Classify and surface a non-leaking error envelope for upstream LLM/media errors."""
    classified = classify_api_error(e, model=op)
    logger.warning("media operation failed", extra={"operation": op, "reason": classified.reason.value, "status_code": classified.status_code, "error": str(e)})
    return classified_http_exception(classified)


def _http_error(status: int, code: str, reason: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "reason": reason, "status": status})


def _resolve_mime_type(content_type: str | None) -> str:
    """Normalize content_type to a MIME type accepted by MiMo ASR."""
    ct = content_type or ""
    if ct in ("audio/mp3", "audio/mpeg"):
        return "audio/mpeg"
    return "audio/wav"


def _upload_size_or_none(audio_file: UploadFile) -> int | None:
    """Best-effort Content-Length probe for an UploadFile."""
    headers = getattr(audio_file, "headers", None)
    if headers is not None:
        raw = headers.get("content-length")
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    spool = getattr(audio_file, "file", None)
    if spool is not None:
        try:
            pos = spool.tell()
            spool.seek(0, 2)
            size = spool.tell()
            spool.seek(pos)
            if size:
                return size
        except Exception:
            return None
    return None


# ── File Service (无需鉴权的临时文件访问) ───────────────────────────────


@router.get("/files/{file_id}")
async def serve_file(file_id: str) -> FileResponse:
    """Serve a temporary media file. No auth required (public URL for LLM access)."""
    result = get_file_path(file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found or expired")
    path, content_type = result
    return FileResponse(path, media_type=content_type)


# ── STT (语音识别) ─────────────────────────────────────────────────────


@router.post("/stt")
@limiter.limit(f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def speech_to_text(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    audio_file: UploadFile = File(...),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
) -> dict[str, Any]:
    """Speech-to-text via the provider chain (MiMo ASR; only MiMo registers STT)."""
    user, _ = auth_data

    declared_size = _upload_size_or_none(audio_file)
    if declared_size is not None and declared_size > STT_MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)", "reason": "payload_too_large", "status": 413},
        )

    if declared_size is not None:
        file_bytes = await audio_file.read()
    else:
        sink = bytearray()
        async for chunk in audio_file.stream():
            sink.extend(chunk)
            if len(sink) > STT_MAX_AUDIO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)", "reason": "payload_too_large", "status": 413},
                )
        file_bytes = bytes(sink)

    mime_type = _resolve_mime_type(audio_file.content_type)

    try:
        with SESSION_LOCAL() as db:
            chain = resolve_provider_chain(db, user.id, "stt")
        if not chain:
            raise missing_config_http("STT")
        result = await execute_with_fallback(
            db=None,
            user_id=user.id,
            service_type="stt",
            call_fn=lambda p: p.transcribe(file_bytes, mime_type=mime_type, language="auto"),
            _chain=chain,
        )
        return {"success": True, "text": result.text}
    except HTTPException:
        raise
    except MissingLlmConfigError:
        raise missing_config_http("STT")
    except Exception as e:
        raise _llm_http_error(e, "stt") from e


# ── TTS (语音合成) ─────────────────────────────────────────────────────


@router.post("/tts")
@limiter.limit(f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def text_to_speech(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    text: str = Form(...),
    voice: str = Form(default=""),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
) -> StreamingResponse:
    """Text-to-speech via the provider chain (MiMo TTS or MiniMax TTS)."""
    user, _ = auth_data
    if not text:
        raise HTTPException(status_code=400, detail={"error": "text is required", "reason": "missing_params", "status": 400})
    if len(text) > TTS_MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail={"error": f"text exceeds {TTS_MAX_TEXT_CHARS} chars", "reason": "payload_too_large", "status": 413},
        )

    try:
        with SESSION_LOCAL() as db:
            chain = resolve_provider_chain(db, user.id, "tts")
        if not chain:
            raise missing_config_http("TTS")
        result = await execute_with_fallback(
            db=None,
            user_id=user.id,
            service_type="tts",
            call_fn=lambda p: p.synthesize(text, voice=pick_voice_id(voice, p.provider_name)),
            _chain=chain,
        )
    except HTTPException:
        raise
    except MissingLlmConfigError:
        raise missing_config_http("TTS")
    except Exception as e:
        raise _llm_http_error(e, "tts") from e

    # Report the actually-used voice so the desktop stays in sync after provider substitution.
    return StreamingResponse(iter([result.audio]), media_type=result.mime, headers={"X-Voice-Used": result.voice or ""})


# ── Image Generation (图片生成) ────────────────────────────────────────


@router.post("/image_gen")
@limiter.limit(f"{SETTINGS.media_image_gen_rate_limit_per_minute}/minute")
async def image_gen(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    prompt: str = Form(...),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
) -> dict[str, Any]:
    """Image generation via the provider chain. Returns 501 when no image-gen provider is configured."""
    user, _ = auth_data
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required", "reason": "missing_params", "status": 400})

    try:
        with SESSION_LOCAL() as db:
            chain = resolve_provider_chain(db, user.id, "image_gen")
        if not chain:
            raise missing_config_http("image_gen", status_code=501)
        result = await execute_with_fallback(
            db=None,
            user_id=user.id,
            service_type="image_gen",
            call_fn=lambda p: p.generate(ImageGenRequest(prompt=prompt)),
            _chain=chain,
        )
    except HTTPException:
        raise
    except MissingLlmConfigError:
        raise missing_config_http("image_gen", status_code=501)
    except Exception as e:
        raise _llm_http_error(e, "image_gen") from e

    if not result.images:
        raise _http_error(502, "image_gen_empty", "图片生成服务返回空结果")

    asset = result.images[0]
    if asset.url:
        # DALL·E-style URL — pass through (URL is provider-hosted, usually
        # valid for an hour).
        return {"success": True, "url": asset.url}
    # base64 payload — persist locally and serve via our public files route so
    # downstream callers (LLM image_url parts, browser previews) get a stable
    # URL that survives MiniMax CDN eviction.
    data = base64.b64decode(asset.b64 or "")
    file_id, public_url = save_file(data, session_id="", content_type=asset.mime, ext="jpg")
    return {"success": True, "url": public_url}


# ── Video Generation (视频生成) ───────────────────────────────────────


@router.post("/video_gen")
@limiter.limit(f"{SETTINGS.media_video_gen_rate_limit_per_minute}/minute")
async def video_gen(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    prompt: str = Form(...),
    duration: int = Form(default=6),
    resolution: str = Form(default="768P"),
    first_frame_image: str | None = Form(default=None),
    aspect_ratio: str | None = Form(default=None),
    model: str | None = Form(default=None),
    wait_seconds: int = Form(default=0),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
) -> dict[str, Any]:
    """Submit a video generation job. Default response is 202 + task_id; if
    ``wait_seconds`` > 0, the handler polls for up to that many seconds and
    returns the resulting URL directly when the job finishes."""
    user, _ = auth_data
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required", "reason": "missing_params", "status": 400})
    # Permissive union of the v1 (Hailuo: 6/10s, 512P/768P/1080P) and v2
    # (H3: 4-15s, 768P/2K) parameter spaces — the exact per-model rules are
    # enforced in the provider and surface via _llm_http_error below.
    if not 4 <= duration <= 15:
        raise HTTPException(status_code=400, detail={"error": "duration must be between 4 and 15 seconds", "reason": "invalid_params", "status": 400})
    if resolution not in ("512P", "768P", "1080P", "2K"):
        raise HTTPException(status_code=400, detail={"error": "resolution must be 512P/768P/1080P/2K", "reason": "invalid_params", "status": 400})
    if wait_seconds < 0 or wait_seconds > 60:
        wait_seconds = min(max(wait_seconds, 0), 60)

    try:
        with SESSION_LOCAL() as db:
            job = await enqueue_video_job(
                db,
                user_id=user.id,
                session_id=None,
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                first_frame_image=first_frame_image,
                model=model,
                aspect_ratio=aspect_ratio,
            )
    except MissingLlmConfigError:
        raise _http_error(501, "video_gen_not_configured", "视频生成服务未配置。请在设置中配置 VIDEO_GEN_BASE_URL 和 VIDEO_GEN_API_KEY。")
    except Exception as e:
        raise _llm_http_error(e, "video_gen") from e

    if wait_seconds > 0:
        # Bounded pseudo-sync: poll the DB for status until deadline. Most
        # MiniMax generations complete well within 60s for short clips; for
        # longer ones the caller polls ``GET /video_gen/{id}`` instead.
        deadline = naive_utc_now() + timedelta(seconds=wait_seconds)
        while naive_utc_now() < deadline:
            await asyncio.sleep(2)
            with SESSION_LOCAL() as db:
                row = get_job(db, job.id, user.id)
                if row is None:
                    break
                if row.status == "succeeded":
                    return {"success": True, "task_id": str(job.id), "status": "succeeded", "url": row.video_url}
                if row.status == "failed":
                    return {"success": False, "task_id": str(job.id), "status": "failed", "error": row.error_message}

    return {
        "success": True,
        "task_id": str(job.id),
        "status": job.status,
        "poll_url": f"/api/media/video_gen/{job.id}",
    }


@router.get("/video_gen/{task_id}")
async def video_gen_status(
    task_id: int,
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
) -> dict[str, Any]:
    user, _ = auth_data
    with SESSION_LOCAL() as db:
        row = get_job(db, task_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "video job not found", "reason": "not_found", "status": 404})
    return {
        "task_id": str(row.id),
        "status": row.status,
        "url": row.video_url,
        "error": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
