from typing import Any
from urllib.parse import quote

from common import get_router
from components import SETTINGS, STT_MAX_AUDIO_BYTES, TTS_MAX_TEXT_CHARS, get_file_path, get_logger
from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from modules.auth import LoginRecord, User, get_current_session
from services.llm import MissingLlmConfigError, classify_api_error, synthesize_speech, transcribe_audio
from services.rate_limit import limiter

from ._http_errors import classified_http_exception, missing_config_http

logger = get_logger(__name__)

_UPLOAD_CHUNK_BYTES = 1024 * 1024

router = get_router()


def _llm_http_error(e: Exception, op: str) -> HTTPException:
    """分类上游 LLM/media 错误并返回非泄露错误信封。"""
    classified = classify_api_error(e, model=op)
    # exc_info 保留完整 traceback 在服务端日志（API 响应仍非泄露，仅分类 reason+message 触达 renderer），便于事后排查 TTS/STT/生图侧翻时的真实异常链。
    logger.warning("media operation failed", extra={"operation": op, "reason": classified.reason.value, "status_code": classified.status_code, "error": str(e)}, exc_info=True)
    return classified_http_exception(classified)


def _resolve_mime_type(content_type: str | None) -> str:
    """把 content_type 归一为 ASR 支持的 MIME 类型。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("audio/mp3", "audio/mpeg"):
        return "audio/mpeg"
    if ct in ("audio/webm", "video/webm"):
        return "audio/webm"
    if ct in ("audio/ogg", "audio/opus"):
        return "audio/ogg"
    if ct in ("audio/mp4", "audio/m4a", "video/mp4"):
        return "audio/mp4"
    return "audio/wav"


def _upload_size_or_none(audio_file: UploadFile) -> int | None:
    """尽力探测 UploadFile 的 Content-Length。"""
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


@router.get("/files/{file_id}")
async def serve_file(file_id: str) -> FileResponse:
    """提供临时媒体文件（公开 URL，供 LLM 访问，无需鉴权）。"""
    result = get_file_path(file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found or expired")
    path, content_type = result
    return FileResponse(path, media_type=content_type)


@router.post("/stt")
@limiter.limit(f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def speech_to_text(
    request: Request,
    audio_file: UploadFile | None = File(None),
    file: UploadFile | None = File(None),
    auth_data: tuple[User, LoginRecord] = Depends(get_current_session),
) -> dict[str, Any]:
    """走供应商链路的语音转写（仅 MiMo 注册了 STT）。"""
    user, _ = auth_data
    target_file = audio_file or file
    if target_file is None:
        raise HTTPException(
            status_code=422,
            detail={"error": "Missing audio file", "reason": "missing_audio_file", "status": 422},
        )

    # 客户端 multipart 头声明的 size 仅用于拦截明显超大的上传；下方流式 cap 才是真正限制——信任 header 会让攻击者把任意大小数据读进内存。
    declared_size = _upload_size_or_none(target_file)
    if declared_size is not None and declared_size > STT_MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)", "reason": "payload_too_large", "status": 413},
        )

    sink = bytearray()
    while chunk := await target_file.read(_UPLOAD_CHUNK_BYTES):
        sink.extend(chunk)
        if len(sink) > STT_MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)", "reason": "payload_too_large", "status": 413},
            )
    file_bytes = bytes(sink)

    mime_type = _resolve_mime_type(target_file.content_type)

    try:
        text = await transcribe_audio(user.id, file_bytes, mime_type)
        return {"success": True, "text": text}
    except HTTPException:
        raise
    except MissingLlmConfigError:
        raise missing_config_http("STT")
    except Exception as e:
        raise _llm_http_error(e, "stt") from e


async def _extract_request_data(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


@router.post("/tts")
@limiter.limit(f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def text_to_speech(request: Request, auth_data: tuple[User, LoginRecord] = Depends(get_current_session)) -> StreamingResponse:
    """走供应商链路的语音合成（MiMo TTS 或 MiniMax TTS），接受 JSON 或 Form body。"""
    user, _ = auth_data
    data = await _extract_request_data(request)
    text = str(data.get("text") or "").strip()
    voice = str(data.get("voice") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail={"error": "text is required", "reason": "missing_params", "status": 400})
    if len(text) > TTS_MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail={"error": f"text exceeds {TTS_MAX_TEXT_CHARS} chars", "reason": "payload_too_large", "status": 413})

    try:
        result = await synthesize_speech(user.id, text, voice)
    except HTTPException:
        raise
    except MissingLlmConfigError:
        raise missing_config_http("TTS")
    except Exception as e:
        raise _llm_http_error(e, "tts") from e

    # 回传实际使用的音色，让 desktop 在供应商切换后保持同步。
    return StreamingResponse(iter([result.audio]), media_type=result.mime, headers={"X-Voice-Used": quote(result.voice or "", safe="")})
