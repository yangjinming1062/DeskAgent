import asyncio
import base64
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from common import get_router
from components import SESSION_LOCAL, SETTINGS, STT_MAX_AUDIO_BYTES, TTS_MAX_TEXT_CHARS, get_file_path, get_logger, save_file, utc_now
from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from modules.auth import LoginRecord, User, get_current_session
from services.llm import ImageGenRequest, MissingLlmConfigError, classify_api_error, execute_with_fallback, pick_voice_id, resolve_provider_chain
from services.media import enqueue_video_job, get_job
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


def _http_error(status: int, code: str, reason: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "reason": reason, "status": status})


def _resolve_mime_type(content_type: str | None) -> str:
    """把 content_type 归一为 MiMo ASR 支持的 MIME 类型。"""
    ct = content_type or ""
    if ct in ("audio/mp3", "audio/mpeg"):
        return "audio/mpeg"
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
async def speech_to_text(request: Request, audio_file: UploadFile = File(...), auth_data: tuple[User, LoginRecord] = Depends(get_current_session)) -> dict[str, Any]:
    """走供应商链路的语音转写（仅 MiMo 注册了 STT）。"""
    user, _ = auth_data

    # 客户端 multipart 头声明的 size 仅用于拦截明显超大的上传；下方流式 cap 才是真正限制——信任 header 会让攻击者把任意大小数据读进内存。
    declared_size = _upload_size_or_none(audio_file)
    if declared_size is not None and declared_size > STT_MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413, detail={"error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)", "reason": "payload_too_large", "status": 413}
        )

    sink = bytearray()
    while chunk := await audio_file.read(_UPLOAD_CHUNK_BYTES):
        sink.extend(chunk)
        if len(sink) > STT_MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=413, detail={"error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)", "reason": "payload_too_large", "status": 413}
            )
    file_bytes = bytes(sink)

    mime_type = _resolve_mime_type(audio_file.content_type)

    try:
        async with SESSION_LOCAL() as db:
            chain = await resolve_provider_chain(db, user.id, "stt")
        if not chain:
            raise missing_config_http("STT")
        result = await execute_with_fallback(
            db=None, user_id=user.id, service_type="stt", call_fn=lambda p: p.transcribe(file_bytes, mime_type=mime_type, language="auto"), _chain=chain
        )
        return {"success": True, "text": result.text}
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
        async with SESSION_LOCAL() as db:
            chain = await resolve_provider_chain(db, user.id, "tts")
        if not chain:
            raise missing_config_http("TTS")
        result = await execute_with_fallback(
            db=None, user_id=user.id, service_type="tts", call_fn=lambda p: p.synthesize(text, voice=pick_voice_id(voice, p.provider_name)), _chain=chain
        )
    except HTTPException:
        raise
    except MissingLlmConfigError:
        raise missing_config_http("TTS")
    except Exception as e:
        raise _llm_http_error(e, "tts") from e

    # 回传实际使用的音色，让 desktop 在供应商切换后保持同步。
    return StreamingResponse(iter([result.audio]), media_type=result.mime, headers={"X-Voice-Used": quote(result.voice or "", safe="")})


@router.post("/image_gen")
@limiter.limit(f"{SETTINGS.media_image_gen_rate_limit_per_minute}/minute")
async def image_gen(request: Request, auth_data: tuple[User, LoginRecord] = Depends(get_current_session)) -> dict[str, Any]:
    """走供应商链路的图片生成，接受 JSON 或 Form body。"""
    user, _ = auth_data
    data = await _extract_request_data(request)
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required", "reason": "missing_params", "status": 400})

    try:
        async with SESSION_LOCAL() as db:
            chain = await resolve_provider_chain(db, user.id, "image_gen")
        if not chain:
            raise missing_config_http("image_gen", status_code=501)
        result = await execute_with_fallback(db=None, user_id=user.id, service_type="image_gen", call_fn=lambda p: p.generate(ImageGenRequest(prompt=prompt)), _chain=chain)
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
        # DALL·E 风格 URL 直传（供应商托管，通常一小时有效）。
        return {"success": True, "url": asset.url}
    # base64 载荷本地持久化并通过公开 files 路由提供，让下游调用方（LLM image_url 部分、浏览器预览）拿到稳定 URL，不受 MiniMax CDN 清理影响。
    try:
        data_bytes = base64.b64decode(asset.b64 or "")
    except ValueError:
        raise _http_error(502, "image_gen_invalid_payload", "图片生成服务返回了无法解码的数据") from None
    file_id, public_url = save_file(data_bytes, session_id="", content_type=asset.mime, ext="jpg")
    return {"success": True, "url": public_url}


@router.post("/video_gen")
@limiter.limit(f"{SETTINGS.media_video_gen_rate_limit_per_minute}/minute")
async def video_gen(request: Request, auth_data: tuple[User, LoginRecord] = Depends(get_current_session)) -> dict[str, Any]:
    """提交视频生成任务：默认 202 + task_id；若 wait_seconds > 0 则轮询至截止并直接返回最终 URL。"""
    user, _ = auth_data
    data = await _extract_request_data(request)
    prompt = str(data.get("prompt") or "").strip()
    duration = int(data.get("duration") or 6)
    resolution = str(data.get("resolution") or "768P")
    first_frame_image = data.get("first_frame_image") or None
    aspect_ratio = data.get("aspect_ratio") or None
    model = data.get("model") or None
    wait_seconds = int(data.get("wait_seconds") or 0)
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required", "reason": "missing_params", "status": 400})
    # 兼容 v1（Hailuo：6/10s，512P/768P/1080P）与 v2（H3：4-15s，768P/2K）参数空间；逐模型精确校验由供应商完成并通过 _llm_http_error 抛出。
    if not 4 <= duration <= 15:
        raise HTTPException(status_code=400, detail={"error": "duration must be between 4 and 15 seconds", "reason": "invalid_params", "status": 400})
    if resolution not in ("512P", "768P", "1080P", "2K"):
        raise HTTPException(status_code=400, detail={"error": "resolution must be 512P/768P/1080P/2K", "reason": "invalid_params", "status": 400})
    wait_seconds = min(max(wait_seconds, 0), 60)

    try:
        async with SESSION_LOCAL() as db:
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
        # 有界伪同步：轮询 DB 至截止；短片段多数 MiniMax 生成 60s 内完成，更长片段调用方改轮询 GET /video_gen/{id}。
        deadline = utc_now() + timedelta(seconds=wait_seconds)
        while utc_now() < deadline:
            await asyncio.sleep(2)
            async with SESSION_LOCAL() as db:
                row = await get_job(db, job.id, user.id)
                if row is None:
                    break
                if row.status == "succeeded":
                    return {"success": True, "task_id": str(job.id), "status": "succeeded", "url": row.video_url}
                if row.status == "failed":
                    return {"success": False, "task_id": str(job.id), "status": "failed", "error": row.error_message or "视频生成失败，请稍后重试", "reason": row.error_reason}

    return {"success": True, "task_id": str(job.id), "status": job.status, "poll_url": f"/api/media/video_gen/{job.id}"}


@router.get("/video_gen/{task_id}")
async def video_gen_status(task_id: int, auth_data: tuple[User, LoginRecord] = Depends(get_current_session)) -> dict[str, Any]:
    user, _ = auth_data
    async with SESSION_LOCAL() as db:
        row = await get_job(db, task_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "video job not found", "reason": "not_found", "status": 404})
    return {
        "task_id": str(row.id),
        "status": row.status,
        "url": row.video_url,
        "error": row.error_message,
        "reason": row.error_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
