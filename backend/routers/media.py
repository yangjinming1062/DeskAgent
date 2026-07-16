import base64

from config import SETTINGS
from constants import RECORDING_MAX_VIDEO_BYTES
from constants import STT_MAX_AUDIO_BYTES
from constants import TTS_MAX_TEXT_CHARS
from core import classify_api_error
from core import client_for_service
from core import get_file_path
from core import limiter
from core import MissingLlmConfigError
from core import save_file
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from logger import get_logger
from models import LoginRecord
from models import User
from openai import AsyncOpenAI
from routers._http_errors import classified_http_exception
from slowapi.util import get_remote_address
from utils import get_current_session
from utils import SESSION_LOCAL

logger = get_logger(__name__)

ROUTER = APIRouter(prefix="/media", tags=["media"])


# ── Helpers ────────────────────────────────────────────────────────────


def _service_client(user: User, service_type: str) -> tuple[AsyncOpenAI, str]:
    """Resolve ``(client, model_name)`` for a given service type.

    Raises ``HTTPException(400)`` when the provider is not configured.
    """
    try:
        with SESSION_LOCAL() as db:
            return client_for_service(db, user.id, service_type)
    except MissingLlmConfigError as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "reason": "missing_config", "status": 400})


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


@ROUTER.get("/files/{file_id}")
async def serve_file(file_id: str):
    """Serve a temporary media file. No auth required (public URL for LLM access)."""
    result = get_file_path(file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found or expired")
    path, content_type = result
    return FileResponse(path, media_type=content_type)


# ── STT (语音识别) ─────────────────────────────────────────────────────


@ROUTER.post("/stt")
@limiter.limit(f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def speech_to_text(
    request: Request,
    audio_file: UploadFile = File(...),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
):
    """Speech-to-text via MiMo ASR (chat completions API)."""
    user, _ = auth_data
    client, model_name = _service_client(user, "stt")

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

    b64_audio = base64.b64encode(file_bytes).decode("utf-8")
    mime_type = _resolve_mime_type(audio_file.content_type)

    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{b64_audio}"}}]}],
            extra_body={"asr_options": {"language": "auto"}},
        )
        return {"success": True, "text": response.choices[0].message.content}
    except Exception as e:
        raise _llm_http_error(e, "stt") from e


# ── TTS (语音合成) ─────────────────────────────────────────────────────

from constants import TTS_VOICES


@ROUTER.post("/tts")
@limiter.limit(f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def text_to_speech(
    request: Request,
    text: str = Form(...),
    voice: str = Form(default=""),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
):
    """Text-to-speech via MiMo TTS (chat completions API)."""
    user, _ = auth_data
    if not text:
        raise HTTPException(status_code=400, detail={"error": "text is required", "reason": "missing_params", "status": 400})
    if len(text) > TTS_MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail={"error": f"text exceeds {TTS_MAX_TEXT_CHARS} chars", "reason": "payload_too_large", "status": 413},
        )

    client, model_name = _service_client(user, "tts")
    effective_voice = voice or SETTINGS.tts_default_voice

    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text},
            ],
            audio={"format": "mp3", "voice": effective_voice},
        )
        audio_b64 = response.choices[0].message.audio.data
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as e:
        raise _llm_http_error(e, "tts") from e

    return StreamingResponse(iter([audio_bytes]), media_type="audio/mpeg")


# ── Image Generation (图片生成) ────────────────────────────────────────


@ROUTER.post("/image_gen")
@limiter.limit(f"{SETTINGS.media_image_gen_rate_limit_per_minute}/minute")
async def image_gen(
    request: Request,
    prompt: str = Form(...),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
):
    """Image generation. Returns 501 when no image-gen provider is configured."""
    user, _ = auth_data
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required", "reason": "missing_params", "status": 400})

    try:
        client, model_name = _service_client(user, "image_gen")
    except HTTPException:
        raise _http_error(501, "image_gen_not_configured", "图片生成服务未配置。请在设置中配置 IMAGE_GEN_BASE_URL 和 IMAGE_GEN_API_KEY。")

    try:
        response = await client.images.generate(model=model_name, prompt=prompt, n=1, size="1024x1024")
        return {"success": True, "url": response.data[0].url}
    except Exception as e:
        raise _llm_http_error(e, "image_gen") from e


# ── Recording Upload (屏幕录制上传) ────────────────────────────────────


@ROUTER.post("/recording/upload")
@limiter.limit(f"{SETTINGS.media_recording_upload_rate_limit_per_minute}/minute")
@limiter.limit(f"{SETTINGS.media_recording_upload_rate_limit_per_ip_per_minute}/minute", key_func=get_remote_address)
async def upload_recording(
    request: Request,
    file: UploadFile = File(...),
    ext: str = Form(default=""),
    session_id: str = Form(default=""),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
):
    """Upload a screen-recording webm and return a public HTTP URL."""
    raw_ext = ext.strip().lstrip(".").lower()
    if not raw_ext:
        raise _http_error(415, "unable_to_determine_extension", "ext field is required")
    if raw_ext != "webm":
        raise _http_error(415, "invalid_mime_type", f"file extension {raw_ext!r} not supported (only webm)")

    max_mb = RECORDING_MAX_VIDEO_BYTES // (1024 * 1024)
    sink = bytearray()
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        sink.extend(chunk)
        if len(sink) > RECORDING_MAX_VIDEO_BYTES:
            raise _http_error(413, "payload_too_large", f"Video file too large (max {max_mb} MB)")

    if not sink:
        raise _http_error(400, "empty_file", "uploaded file has zero bytes")

    _, auth_data_inner = auth_data
    effective_session_id = session_id or str(auth_data_inner.user_id)
    file_id, file_url = save_file(sink, effective_session_id, "video/webm", raw_ext)

    return {"file_url": file_url, "file_id": file_id, "content_type": "video/webm", "size_bytes": len(sink)}
