import asyncio
import json
from dataclasses import replace
from datetime import timedelta

from components import SESSION_LOCAL, SETTINGS, download_capped, get_logger, save_file, utc_now
from modules.media import VideoGenJob
from modules.ws import WSEvent
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import MissingLlmConfigError, ServiceType, VideoGenProvider, VideoGenRequest, execute_with_fallback, resolve, resolve_provider_chain

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()

_BG_TASKS: set[asyncio.Task] = set()


async def drain() -> None:
    """取消并等待所有后台视频任务完成，吞下 CancelledError。"""
    if not _BG_TASKS:
        return
    pending = list(_BG_TASKS)
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _update_job(job_id: int, **fields) -> None:
    """用全新短会话更新任务行——后台任务比请求会话长寿，绝不复用调用方的 ``db``；行在读写之间被 GC（管理员 DELETE 等）则提前返回。"""

    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await db.commit()


async def _emit_ws_event(user_id: int, event_type: str, payload: dict) -> None:
    """将 WSEvent 行写入 PostgreSQL outbox；PostgreSQL NOTIFY 触发后由 ws_events worker 投递给已连接客户端，确保 REST 离线提交或 WS 中途重连的进度也能送达。"""
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    async with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type=event_type, payload=payload_json))
        await db.commit()


async def get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoGenJob | None:
    """按 user_id 过滤，避免 GET 接口泄露其他用户的任务。"""

    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def enqueue_video_job(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: str | None,
    prompt: str,
    duration: int,
    resolution: str,
    first_frame_image: str | None,
    model: str | None,
    aspect_ratio: str | None,
) -> "VideoGenJob":
    """插入 queued 任务行、向供应商提交并调度后台轮询任务，返回持久化行；任务 id 属于特定供应商，轮询始终钉在提交成功的供应商上。"""
    req = VideoGenRequest(prompt=prompt, duration=duration, resolution=resolution, first_frame_image=first_frame_image, aspect_ratio=aspect_ratio, model=model)

    params = {"duration": duration, "resolution": resolution, "first_frame_image": first_frame_image, "aspect_ratio": aspect_ratio}

    # 捕获提交实际胜出的供应商，轮询/下载都走它（task_id 跨供应商不通用）。
    submitted_provider: VideoGenProvider | None = None

    async def _submit(p):
        nonlocal submitted_provider
        submitted_provider = p
        return await p.submit(req)

    # 解析一次链：chain[0] 写入任务行的 provider 元数据，execute_with_fallback 在同一环境下重新解析得到同一头部。
    chain = await resolve_provider_chain(db, user_id, "video_gen")
    if not chain:
        raise MissingLlmConfigError("no provider configured for service 'video_gen'")
    head_cfg = chain[0]
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
        provider=head_cfg.provider_name,
        model=req.model or head_cfg.model,
        prompt=prompt,
        params_json=json.dumps(params),
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        submitted = await execute_with_fallback(db, user_id, "video_gen", call_fn=_submit)
    except Exception as e:
        logger.exception("video submit failed", extra={"job_id": job.id})
        try:
            await _record_failure(job.id, reason="submit_failed", exc=e)
        except Exception as update_err:
            logger.exception("failed to mark job as failed", extra={"job_id": job.id, "error": str(update_err)})
        raise

    if submitted_provider is not None and submitted_provider.provider_name != job.provider:
        job.provider = submitted_provider.provider_name
        job.model = req.model or submitted_provider.config.model
    job.provider_task_id = submitted.task_id
    await db.commit()

    t = asyncio.create_task(_poll_and_finalize(job.id))
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return job


_FAILURE_COPY: dict[str, str] = {
    "submit_failed": "视频提交失败，请稍后重试",
    "missing_task_id": "视频服务暂不可用，请稍后重试",
    "provider_unavailable": "视频 provider 配置变更，请稍后重试",
    "provider_failed": "视频生成失败，请稍后重试",
    "download_failed": "视频下载失败，请稍后重试",
    "download_interrupted": "视频下载中断，请重新生成",
    "timeout": "视频生成超时，请稍后重试",
    "poll_failed": "视频生成失败，请稍后重试",
    "worker_failed": "视频生成服务异常，请稍后重试",
}

_POLICY_KEYWORDS = ("policy", "unsafe", "content_filter", "敏感", "违规", "moderation")


def _failure_user_message(reason: str, exc: BaseException | None) -> str:
    """按失败原因挑选预设文案；策略审核相关的异常文本仅做关键词嗅探，原始异常文本绝不流到渲染端（ARCH §11#2）。"""
    msg = _FAILURE_COPY.get(reason, "视频生成失败，请稍后重试")
    if exc is not None:
        text = str(exc).lower()
        if any(k in text for k in _POLICY_KEYWORDS):
            return "内容审核未通过，请调整提示词后重试"
    return msg


async def _record_failure(job_id: int, *, reason: str, exc: BaseException | None = None, user_id: int | None = None, exc_text: str | None = None) -> None:
    """写入脱敏后的失败行与对应 WSEvent；``exc`` 仅服务端记录，``error_message`` 与 WS 事件载荷只携带预设文案——原始供应商文本与内部字符串绝不外泄。"""
    if exc is not None:
        logger.exception("video job failure", extra={"job_id": job_id, "reason": reason})
    elif exc_text is not None:
        logger.warning("video job failure", extra={"job_id": job_id, "reason": reason, "raw": exc_text[:200]})
    else:
        logger.warning("video job failure", extra={"job_id": job_id, "reason": reason})
    sniff_exc: BaseException | None = exc if exc is not None else (RuntimeError(exc_text) if exc_text else None)
    user_msg = _failure_user_message(reason, sniff_exc)
    await _update_job(job_id, status="failed", error_reason=reason, error_message=user_msg)
    if user_id is None:
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id)
            user_id = row.user_id if row else 0
    if user_id:
        await _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": user_msg})


# In-flight 集合：进程中途重启时，多个协程可能竞争 finalize 同一任务。第一个进入的注册，后续提前退出，避免重复下载或重复 WSEvent；集合驻留在进程内存（重启即丢失——重启后由 resume_pending_video_jobs 走 DB 重建）。


async def _poll_and_finalize(job_id: int) -> None:
    """后台主循环：轮询供应商、成功后下载、写 WSEvent。状态机：``queued`` → ``processing`` → ``downloading`` → ``succeeded``/``failed``；``downloading`` 故意排除在 resume 集合外，避免重连任务重启下载半段。"""
    if job_id in _INFLIGHT:
        return
    _INFLIGHT.add(job_id)
    try:
        await _poll_and_finalize_locked(job_id)
    finally:
        _INFLIGHT.discard(job_id)


async def _poll_and_finalize_locked(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        # 幂等护栏：上一次已终结的行不再重复下载/发事件；``_update_job`` 内部的终态检查是双保险。
        if job.status in ("succeeded", "failed"):
            logger.info("skipping already-finalized job", extra={"job_id": job_id, "status": job.status})
            return
        user_id = job.user_id
        provider_task_id = job.provider_task_id or ""

    async def _evt(event_type: str, payload: dict) -> None:
        await _emit_ws_event(user_id, event_type, payload)

    try:
        if not provider_task_id:
            # 提交完成但 task_id 未持久化（极小概率，但保持防御），快速失败并给出明确原因，避免行一直处于 limbo。
            await _record_failure(job_id, reason="missing_task_id", user_id=user_id)
            return

        async with SESSION_LOCAL() as db:
            # 重新解析链并挑选与本任务 ``provider`` 列匹配的槽位（由 ``enqueue_video_job`` 提交成功时写入）。轮询必须命中拥有该 ``task_id`` 的供应商——task_id 跨供应商不通用。
            job_row = await db.get(VideoGenJob, job_id)
            provider_name = job_row.provider if job_row else ""
            job_model = (job_row.model if job_row else "") or ""
            chain = await resolve_provider_chain(db, user_id, "video_gen")
            provider_cfg = next((cfg for cfg in chain if cfg.provider_name == provider_name), None)
        # 将配置钉在任务提交时的 model 上。供应商可能按模型名切换 API 协议（MiniMax v1 vs H3 v2），用户中途改动 model 配置会让重新解析的链命中错误接口。
        if provider_cfg is not None and job_model and job_model != provider_cfg.model:
            provider_cfg = replace(provider_cfg, model=job_model)
        if provider_cfg is None:
            await _record_failure(job_id, reason="provider_unavailable", user_id=user_id)
            return
        provider = resolve(ServiceType.video_gen, provider_cfg.provider_name)(provider_cfg)

        interval = SETTINGS.video_gen_poll_interval_seconds
        deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
        while True:
            # 重新加载行以感知并发终态更新（如用户 DELETE 行、其他 worker 已终结）。provider_task_id 为空表示行被中途清空。
            async with SESSION_LOCAL() as db:
                job = await db.get(VideoGenJob, job_id)
                if job is None or job.status in ("succeeded", "failed"):
                    return
                current_task_id = job.provider_task_id or provider_task_id

            try:
                status = await provider.poll(current_task_id)
            except Exception:
                logger.exception("video poll failed", extra={"job_id": job_id})
                await _record_failure(job_id, reason="poll_failed", user_id=user_id)
                return

            if status.status == "succeeded":
                # 在 ``downloading`` 状态认领该行——resume_pending_video_jobs 会跳过任何非 queued/processing 状态的任务，因此中途崩溃不会触发第二次下载。
                async with SESSION_LOCAL() as db:
                    claimed = (
                        await db.execute(
                            update(VideoGenJob)
                            .where(VideoGenJob.id == job_id, VideoGenJob.status.notin_(("succeeded", "failed", "downloading")))
                            .values(status="downloading", provider_file_id=status.file_id),
                        )
                    ).rowcount
                    await db.commit()
                    if not claimed:
                        return
                try:
                    file_id, public_url = await _download_and_store(provider, status.file_id, download_url=status.download_url)
                except Exception:
                    logger.exception("video download failed", extra={"job_id": job_id})
                    await _record_failure(job_id, reason="download_failed", user_id=user_id)
                    return
                await _update_job(job_id, status="succeeded", file_id=file_id, video_url=public_url)
                await _evt("video_gen.completed", {"task_id": str(job_id), "url": public_url})
                logger.info("video job succeeded", extra={"job_id": job_id, "file_id": file_id})
                return
            if status.status == "failed":
                await _record_failure(job_id, reason="provider_failed", user_id=user_id, exc_text=status.error)
                return

            await _update_job(job_id, status="processing")
            if utc_now() >= deadline:
                await _record_failure(job_id, reason="timeout", user_id=user_id)
                return
            await asyncio.sleep(interval)
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        await _record_failure(job_id, reason="worker_failed", user_id=user_id)


async def _download_and_store(provider, file_id: str | None, *, download_url: str | None = None) -> tuple[str, str]:
    """从供应商下载视频字节（须在 URL 窗口内）并通过 ``components.save_file`` 本地持久化。MiniMax-H3 v2 在成功路径直接返回 URL（填 ``download_url``），跳过额外 ``fetch()``；旧版 MiniMax-Hailuo v1 把 URL 藏在 ``files/retrieve`` 接口后（填 ``file_id``）。"""
    if download_url:
        asset_content_type = "video/mp4"
    else:
        if not file_id:
            raise RuntimeError("provider.poll succeeded without file_id or download_url")
        asset = await provider.fetch(file_id)
        download_url = asset.download_url
        asset_content_type = asset.content_type or "video/mp4"
    data = await _stream_download(download_url)
    return save_file(data, session_id="", content_type=asset_content_type, ext="mp4")


async def _stream_download(url: str) -> bytes:
    """受限下载，上限为 ``video_gen_download_max_bytes``——LLM 默认 30–60s 读取超时对慢速 200MB 视频远不够，改用 10 分钟。"""
    cap = SETTINGS.video_gen_download_max_bytes
    return await download_capped(url, max_bytes=cap, timeout=600.0)


async def resume_pending_video_jobs() -> None:
    """扫描 queued/processing 任务并重新挂载轮询任务，由 FastAPI lifespan 启动时调用；``downloading`` 是恢复交接点——下载中断但没完成的任务在 9h 供应商 URL 窗口过期后不可恢复，故直接标记失败而非空转。"""

    async with SESSION_LOCAL() as db:
        stuck = await db.execute(
            VideoGenJob.__table__.update()
            .where(VideoGenJob.status == "downloading")
            .values(status="failed", error_reason="download_interrupted", error_message=_FAILURE_COPY["download_interrupted"]),
        )
        rows = (await db.execute(select(VideoGenJob).where(VideoGenJob.status.in_(("queued", "processing"))))).scalars().all()
        job_ids = [r.id for r in rows]
        await db.commit()
    if stuck.rowcount:
        logger.warning("marked downloading jobs failed during resume", extra={"count": stuck.rowcount})
    for job_id in job_ids:
        t = asyncio.create_task(_poll_and_finalize(job_id))
        _BG_TASKS.add(t)
        t.add_done_callback(_BG_TASKS.discard)
    if job_ids:
        logger.info("Resumed pending video jobs", extra={"count": len(job_ids)})
