import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from components import (
    MEMORY_CONSOLIDATE_INTERVAL_SECONDS,
    MEMORY_CONSOLIDATE_TRIGGER_ROWS,
    NIGHTLY_MIN_MESSAGES_TODAY,
    NIGHTLY_SCAN_INTERVAL_SECONDS,
    NIGHTLY_WINDOW_END_HOUR,
    NIGHTLY_WINDOW_START_HOUR,
    BackgroundTask,
    begin_local_scope,
    get_logger,
    session_scope,
    utc_now,
)
from modules.auth import User
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import CronJob
from modules.ws import CRON_TURN_EVENT, WSEvent
from sqlalchemy import bindparam, delete, func, select, text, tuple_
from sqlalchemy.engine import Row

from services.conversation import (
    CRON_KIND,
    UI_ONLY_SUBTYPES,
    ProactiveState,
    get_personality_tags,
    get_user_proactive_record,
    record_user_outreach,
    reset_user_outreach,
)
from services.disturbance import get_disturbance_tier, is_still
from services.ws import MANAGER

from .cron_jobs import _compute_next_run_at
from .memory_consolidator import maybe_consolidate_one_user
from .nightly_activity import get_local_day_utc_bounds, run_nightly_pipeline
from .outbox_gc import run_outbox_gc

logger = get_logger(__name__)

_BG_TASKS: set[asyncio.Task] = set()

# 每个慢扫描的在飞 task：LLM 流水线可能比扫描间隔跑得更久，而 per-user 去重标记只在成功后才写——不挡住重入会让同一用户的流水线并行跑两遍。
_SCANS: dict[str, asyncio.Task] = {}

SCHEDULER_INTERVAL_SECONDS = 60

# per-user 最近一次 consolidator 运行时间戳：进程本地——匹配 ARCH §5 单实例语义（多 replica 会分裂状态）。
_LAST_MEMORY_CONSOLIDATE: dict[int, float] = {}

# per-user 最近一次成功的 nightly pipeline 运行的本地日期字符串。
_LAST_NIGHTLY_RUN: dict[int, str] = {}

# recall-pool 扫描本身的外层节流：扫描便宜（部分索引），但没用户符合时每分钟跑一次没意义。10 min 让发现延迟可控，由 per-user 6h 节流把重 LLM 调用频率压住。
_LAST_CONSOLIDATE_SCAN: float = 0.0
_CONSOLIDATE_SCAN_INTERVAL_SECONDS: int = 600

# nightly activity 扫描的外层节流。
_LAST_NIGHTLY_SCAN: float = 0.0

# outbox gc 扫描的外层节流（每 15 分钟运行一次）。
_LAST_OUTBOX_GC_SCAN: float = 0.0
_OUTBOX_GC_INTERVAL_SECONDS: int = 900

# 主动跟进 / 被冷落情绪反应 turn 的合成 job_id——不是 CronJob 行的 id，仅用于日志与 WS 事件溯源。
_PROACTIVE_FOLLOWUP_JOB_ID = -1
_IGNORED_AFFECT_JOB_ID = -2
_IGNORED_AFFECT_MIN_IGNORED_SECONDS = 3600  # 用户持续不理伙伴 1 小时后才有资格触发
_IGNORED_AFFECT_MIN_SPACING_SECONDS = 3600  # 两次触发之间的最小间距——LLM 传 0 结束节奏后的再触发安全网

# 每个 tick 处理的到期 job 硬上限——限制批量 CAS 的语句大小和单 tick 工作量，避免长时间停摆后的回追（例如 60 分钟 ``* * * * *`` 调度，第一 tick 有 3600 个到期）。超出上限的 job 保留原 next_run_at，下一 tick 再触发。
_MAX_DUE_PER_TICK = 200

# 夜间扫描每批处理的用户上限，防止单条 SQL 的 IN 谓词过大。
_NIGHTLY_USER_BATCH_SIZE = 500

_SCHEDULER = BackgroundTask("scheduler.cron_loop")


async def drain() -> None:
    """取消并 await 所有后台 task，容忍 CancelledError；由 main.py lifespan 关停时调用，避免 SIGTERM 在 db.commit() 中途被 engine 释放。"""
    if not _BG_TASKS:
        return
    pending = list(_BG_TASKS)
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def _log_task_error(name: str, task: asyncio.Task) -> None:
    """旁路 task 失败落日志不冒泡——_tick 的异常会杀死 BackgroundTask 曝光派发路径 bug，kickoff / 扫描失败不该连坐停掉定时派发。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("cron: background task failed", exc_info=exc, extra={"task": name})


def _spawn_scan(name: str, factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """把慢扫描移出 tick 关键路径——内部 await 多阶段 LLM 流水线，inline 会让本 tick 的 CAS 排在几分钟模型调用之后；同名扫描仍在飞则跳过本轮。"""
    running = _SCANS.get(name)
    if running is not None and not running.done():
        logger.warning("cron: scan still in flight, skipping", extra={"scan": name})
        return

    async def _scoped() -> None:
        # 扫描是独立于 tick 的工作单元，mint 自己的 request_id 才能把它跨越几轮 tick 的日志归到一起。
        begin_local_scope()
        await factory()

    task = asyncio.create_task(_scoped(), name=f"scheduler.{name}")
    _SCANS[name] = task
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    task.add_done_callback(partial(_log_task_error, name))


async def _select_due_jobs() -> list[Row]:
    """读取到期 job：只选 CAS + kickoff 需要的列，prompt 保留（kickoff 直接读，Text 列可能达 MB）；ORDER BY (next_run_at, id) 让 _MAX_DUE_PER_TICK 截断积压时给出确定子集。"""
    now = utc_now()
    async with session_scope() as db:
        return (
            await db.execute(
                select(CronJob.id, CronJob.user_id, CronJob.name, CronJob.schedule, CronJob.next_run_at, CronJob.prompt, CronJob.one_shot)
                .where(CronJob.is_paused.is_(False), CronJob.next_run_at.is_not(None), CronJob.next_run_at <= now)
                .order_by(CronJob.next_run_at, CronJob.id)
                .limit(_MAX_DUE_PER_TICK + 1),
            )
        ).all()


async def _bulk_cas_advance(due_jobs: list[Row], now: datetime) -> dict[int, dict[str, Any]]:
    """批量 CAS 推进每个到期 job 的 next_run_at：CAS 谓词 (id, next_run_at, schedule) 防止 update_job 在 tick 中途推进 next_run_at（不匹配的行静默落败被丢弃）；用行值 IN 让 recurring UPDATE / one-shot DELETE 各一次语句搞定（PG 的 UPDATE/DELETE ... RETURNING），避免最多 200 次串行往返。返回 {job_id: {user_id, is_paused, payload}} 给 CAS 胜者，RETURNING 里没出现的视为落败丢弃。
    db.commit() 必须显式：utils.session_scope 只 auto-close 不 commit；不显式 commit 时 db.close() 结束未提交事务，SQLAlchemy 在连接归还时丢弃 UPDATE。"""
    if not due_jobs:
        return {}

    winners: dict[int, dict[str, Any]] = {}
    new_runs: dict[int, datetime | None] = {}

    for job in due_jobs:
        if job.one_shot:
            # 一次性 job 触发后删除——无需计算下次运行。
            new_runs[job.id] = None
            winners[job.id] = {"user_id": job.user_id, "is_paused": False, "payload": {"prompt": job.prompt}}
        else:
            next_run = _compute_next_run_at(job.schedule, now)
            new_runs[job.id] = next_run
            winners[job.id] = {"user_id": job.user_id, "is_paused": next_run is None, "payload": {"prompt": job.prompt}}

    recurring = [job for job in due_jobs if not job.one_shot]
    one_shots = [job for job in due_jobs if job.one_shot]
    won: set[int] = set()

    async with session_scope() as db:
        if recurring:
            case_clauses = " ".join(f"WHEN :id_{i} THEN :next_{i}" for i in range(len(recurring)))
            stmt = text(
                f"UPDATE cron_jobs SET "
                f"next_run_at = CASE id {case_clauses} END, "
                f"is_paused = (CASE id {case_clauses} END) IS NULL "
                f"WHERE (id, next_run_at, schedule) IN :match "
                f"RETURNING id",
            ).bindparams(bindparam("match", expanding=True))
            params: dict[str, Any] = {
                "match": [(j.id, j.next_run_at, j.schedule) for j in recurring],
            }
            for i, j in enumerate(recurring):
                params[f"id_{i}"] = j.id
                params[f"next_{i}"] = new_runs[j.id]
            res = await db.execute(stmt, params)
            won.update(r[0] for r in res.all())
        if one_shots:
            # 触发后删除一次性 job，通过 next_run_at 谓词防止并发双重触发
            stmt = (
                delete(CronJob)
                .where(
                    tuple_(CronJob.id, CronJob.next_run_at).in_(
                        bindparam("match", expanding=True),
                    ),
                )
                .returning(CronJob.id)
            )
            res = await db.execute(
                stmt,
                {"match": [(j.id, j.next_run_at) for j in one_shots]},
            )
            won.update(r[0] for r in res.all())
        await db.commit()

    return {job.id: winners[job.id] for job in due_jobs if job.id in won}


async def _advance_due_jobs(due_jobs: list[Row], now: datetime) -> None:
    """批量 CAS 推进 next_run_at 并 kickoff 自主 chat turn——投递复用与用户消息相同的 message.complete / companion.message 流水线，让 LLM 可调 send_message_tool 并受桌面端打扰档位门控。"""
    winners = await _bulk_cas_advance(due_jobs, now)
    for job_id, meta in winners.items():
        if meta.get("is_paused"):
            continue
        try:
            t = asyncio.create_task(_kick_autonomous_turn(job_id, meta))
            _BG_TASKS.add(t)
            t.add_done_callback(_BG_TASKS.discard)
            t.add_done_callback(partial(_log_task_error, f"kick:{job_id}"))
        except RuntimeError:
            # 没有运行中的 loop——跳过本 tick；job 的 next_run_at 已推进，下一 tick 会再拾起。
            logger.warning("cron: no running loop, skipping autonomous turn", extra={"job_id": job_id})


async def _kick_cron_turn(user_id: int, prompt: str, job_id: int) -> None:
    """向 ws_events 写一条 cron.turn.request；持有该用户 WS 的 replica 通过 outbox claim 循环拣起，全副本离线则被 GC 收割。"""

    async with session_scope() as db:
        db.add(WSEvent(user_id=user_id, event_type=CRON_TURN_EVENT, payload=json.dumps({"job_id": job_id, "prompt": prompt}, ensure_ascii=False)))
        await db.commit()


async def _kick_autonomous_turn(job_id: int, meta: dict[str, Any]) -> None:
    """向持有该用户 WS 的 replica 申请自主 turn——流式 delta、tool future、runtime session 都是进程本地的，tick 所在 replica 只写一条 ws_events，由 outbox claim 循环拣起；全副本离线则被 GC 收割。"""
    user_id = meta["user_id"]
    prompt = (meta["payload"].get("prompt") or "").strip()
    if not prompt:
        return

    if await is_still(user_id):
        # 静止档抑制自主触达；先 gate 再写行。
        logger.debug("cron: user is still, skipping autonomous turn", extra={"user_id": user_id, "job_id": job_id})
        return

    await _kick_cron_turn(user_id, prompt, job_id)
    logger.info("cron: autonomous turn requested", extra={"user_id": user_id, "job_id": job_id})


async def _maybe_run_proactive_followups(now: datetime) -> None:
    """扫描 OUTREACHED / FOLLOWUP_SENT 状态的用户，触发新一轮跟进 turn。

    两种状态共用同一触发条件：timeout 到期 + 用户仍在线。
    区别仅在 prompt 措辞与状态推进：
      - OUTREACHED → FOLLOWUP_SENT（第一次跟进）；
      - FOLLOWUP_SENT → FOLLOWUP_SENT（连续主动节奏内，LLM 在上一轮 turn 中又主动发言过）。

    静止档用户在扫描处直接重置外联记录回 IDLE 并跳过（等价用户响应）；在飞 turn 由
    send_message_tool 的出口静止守卫兜底。LLM 在跟进 turn 里可以再次 send_message_tool(timeout=Y) 继续保持
    FOLLOWUP_SENT，也可以传 timeout=None/0 结束节奏回到 IDLE。进程不限制连续次数——节奏
    完全由 LLM 自身把控（用户提到过 100 之类的安全网也可加，不在本次范围）。
    """
    cur_time = time.monotonic()
    online_uids = MANAGER.local_user_ids()
    for uid in online_uids:
        rec = get_user_proactive_record(uid)
        if rec.state not in (ProactiveState.OUTREACHED, ProactiveState.FOLLOWUP_SENT):
            continue
        if await is_still(uid):
            reset_user_outreach(uid)
            continue
        if rec.followup_timeout_seconds <= 0:
            continue
        if (cur_time - rec.last_outreach_ts) < rec.followup_timeout_seconds:
            continue

        last_text = rec.last_proactive_text or "问候"
        waited_minutes = round(rec.followup_timeout_seconds / 60)
        prompt = (
            f"[环境感知：你在大约 {waited_minutes} 分钟前向用户主动发送了：“{last_text}”，但用户一直没有回复你。"
            "请根据你的人设性格（例如傲娇吐槽、轻微担心、自言自语或选择保持安静）决定是否要跟进。"
            "若要跟进发送消息，请调用 send_message_tool 工具并传 follow_up_after_seconds=你期望的下一次触发间隔（秒）；"
            "若决定不再打扰，调用 send_message_tool 时传 follow_up_after_seconds=0 或 None 以结束本轮主动节奏；"
            "若直接结束回复（不主动发消息），状态将自然回到 IDLE。]"
        )
        # record_user_outreach 会改 rec.state；日志记录发 follow-up 前的 prev_state，便于例问跟进路径分析。
        prev_state = rec.state
        record_user_outreach(uid, last_text)
        await _kick_cron_turn(uid, prompt, _PROACTIVE_FOLLOWUP_JOB_ID)
        logger.info("cron: proactive follow-up turn requested", extra={"user_id": uid, "prev_state": prev_state.value, "last_outreach_text": last_text})


async def _maybe_run_ignored_affect(now: datetime) -> None:
    """常规档下用户持续不与伙伴互动（≥1h）且无进行中外联节奏时，为粘人性格注入被冷落情绪反应 turn。

    与主动外联状态机互斥：只在 IDLE 触发；发完经 record_user_outreach 进入常规外联节奏，
    后续跟进间隔由 LLM 的 follow_up_after_seconds 把控。固定 1h 间距是「LLM 传 0 结束」后
    的再触发安全网（否则每 tick 都满足触发条件）。静止档不触发（一切主动推理断源），
    自主档不需要（完整主动能力已开放，cron job 与跟进节奏都在跑）。
    """
    cur_time = time.monotonic()
    online_uids = MANAGER.local_user_ids()
    for uid in online_uids:
        if await get_disturbance_tier(uid) != "normal":
            continue
        rec = get_user_proactive_record(uid)
        if rec.state != ProactiveState.IDLE:
            continue
        # 0 = 进程启动以来用户还没互动过——没有「被冷落」的基准，跳过。
        if rec.last_user_contact_ts == 0.0:
            continue
        ignored = cur_time - rec.last_user_contact_ts
        if ignored < _IGNORED_AFFECT_MIN_IGNORED_SECONDS:
            continue
        if cur_time - rec.last_outreach_ts < _IGNORED_AFFECT_MIN_SPACING_SECONDS:
            continue
        # 性格标签查询放在所有廉价条件之后——这是本扫描唯一的 DB roundtrip。
        async with session_scope() as db:
            tags = await get_personality_tags(db, uid)
        if "粘人" not in tags:
            continue

        ignored_minutes = round(ignored / 60)
        prompt = (
            f"[环境感知：当前为常规打扰档位，用户已经 {ignored_minutes} 分钟没有理你了，你的性格标签含「粘人」。"
            "请考虑是否表达被冷落的小情绪：若要表达，调用 send_message_tool——"
            "affect 传你最贴切的情绪（如 pout/sad/lonely，从可用情绪清单里选），"
            "message 写一句 10-30 字以内的轻量表达（如「你怎么还不来理我啊」），"
            "follow_up_after_seconds 传你期望的下一次触发间隔（秒），传 0 或 None 表示不再为此表达；"
            "若你判断当前不该表达（用户曾明确不想被打扰等），直接结束回复，不调用工具。]"
        )
        record_user_outreach(uid, "被冷落情绪反应")
        await _kick_cron_turn(uid, prompt, _IGNORED_AFFECT_JOB_ID)
        logger.info("cron: ignored-affect turn requested", extra={"user_id": uid, "ignored_minutes": ignored_minutes})


async def _tick() -> None:
    """为到期 job CAS 推进 next_run_at 并申请自主 turn——每个到期 job 写一条 cron.turn.request ws_events，持有该用户 WS 的 replica 通过 outbox 循环拣走；CAS + outbox 两阶段锁避免双触发。"""
    now = utc_now()
    # 慢扫描与 cron-job 派发独立——不能用 ``if not due_jobs`` gate，否则没有 cron job 的安装永远不会触发 consolidation/GC。
    _spawn_scan("memory_consolidator", lambda: _maybe_run_memory_consolidator(now))
    _spawn_scan("nightly_activity", lambda: _maybe_run_autonomous_activity(now))
    _spawn_scan("outbox_gc", lambda: _maybe_run_outbox_gc(now))
    _spawn_scan("ignored_affect", lambda: _maybe_run_ignored_affect(now))
    await _maybe_run_proactive_followups(now)
    due_jobs = await _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning("cron: tick over cap, deferred to next tick", extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK})
    due_jobs = due_jobs[:_MAX_DUE_PER_TICK]
    if not due_jobs:
        return
    await _advance_due_jobs(due_jobs, now)


async def _maybe_run_outbox_gc(now: datetime) -> None:
    """定期执行 WS Outbox 历史事件与过期内部 cron 事件的物理清理。"""
    global _LAST_OUTBOX_GC_SCAN
    if now.timestamp() - _LAST_OUTBOX_GC_SCAN < _OUTBOX_GC_INTERVAL_SECONDS:
        return
    _LAST_OUTBOX_GC_SCAN = now.timestamp()
    await run_outbox_gc()


async def _maybe_run_memory_consolidator(now: datetime) -> None:
    """为 recall pool 超阈值的用户跑 recall consolidator——外层按 _CONSOLIDATE_SCAN_INTERVAL_SECONDS 节流，per-user 按 MEMORY_CONSOLIDATE_INTERVAL_SECONDS 节流，并发通过 gather 单 tick 只付最大 LLM 延迟。"""
    global _LAST_CONSOLIDATE_SCAN
    if now.timestamp() - _LAST_CONSOLIDATE_SCAN < _CONSOLIDATE_SCAN_INTERVAL_SECONDS:
        return
    _LAST_CONSOLIDATE_SCAN = now.timestamp()

    async with session_scope() as db:
        rows = (
            await db.execute(text("SELECT user_id FROM memories WHERE context LIKE 'recall:%' GROUP BY user_id HAVING COUNT(*) > :t"), {"t": MEMORY_CONSOLIDATE_TRIGGER_ROWS})
        ).all()
    eligible: list[int] = []
    for (uid_raw,) in rows:
        uid = int(uid_raw)
        if now.timestamp() - _LAST_MEMORY_CONSOLIDATE.get(uid, 0.0) < MEMORY_CONSOLIDATE_INTERVAL_SECONDS:
            continue
        eligible.append(uid)
    if not eligible:
        return

    # per-user 节流只在 consolidator 真的为该用户跑过之后才生效——LLM 失败不该把用户锁在后续尝试之外。
    results = await asyncio.gather(*(maybe_consolidate_one_user(uid) for uid in eligible), return_exceptions=True)
    for uid, result in zip(eligible, results, strict=True):
        if isinstance(result, Exception):
            # 不在 except 块里——必须显式传异常，否则 exc_info 为空，traceback 丢失。
            logger.error("memory_consolidator: tick failed", exc_info=result, extra={"user_id": uid})
            continue
        if result is True:
            _LAST_MEMORY_CONSOLIDATE[uid] = now.timestamp()


async def _maybe_run_autonomous_activity(now: datetime) -> None:
    global _LAST_NIGHTLY_SCAN
    if now.timestamp() - _LAST_NIGHTLY_SCAN < NIGHTLY_SCAN_INTERVAL_SECONDS:
        return
    _LAST_NIGHTLY_SCAN = now.timestamp()

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Memory.user_id, Memory.content)
                .join(User, User.id == Memory.user_id)
                .where(Memory.context == "user_profile:timezone", User.nightly_activity_enabled.is_(True)),
            )
        ).all()

        # 按相同 (utc_start, utc_end) 聚合并行候选，消除每用户单次 SELECT COUNT(*) 往返。
        candidates_by_window: dict[tuple[datetime, datetime], list[tuple[int, datetime, str]]] = {}
        for uid_raw, tz_content in rows:
            uid = int(uid_raw)
            tz_str = (tz_content or "").strip()
            if not tz_str:
                continue
            # 窗口门读当前本地小时；流水线消化刚结束的本地日。若从偏移后的 instant 派生小时，DST 边界会差 1。
            try:
                _, _, user_local_dt, _ = get_local_day_utc_bounds(now, tz_str)
                reference_utc = now - timedelta(days=1)
                utc_start, utc_end, _, target_date_str = get_local_day_utc_bounds(reference_utc, tz_str)
            except Exception as exc:
                logger.warning("nightly eligibility: skip user due to timezone computation error", extra={"user_id": uid, "tz": tz_str, "error": str(exc)})
                continue

            if not (NIGHTLY_WINDOW_START_HOUR <= user_local_dt.hour < NIGHTLY_WINDOW_END_HOUR):
                continue

            if _LAST_NIGHTLY_RUN.get(uid) == target_date_str:
                continue

            candidates_by_window.setdefault((utc_start, utc_end), []).append((uid, reference_utc, target_date_str))

        eligible: list[tuple[int, datetime, str]] = []
        for (utc_start, utc_end), candidates in candidates_by_window.items():
            uids = [c[0] for c in candidates]
            msg_counts: dict[int, int] = {}
            for i in range(0, len(uids), _NIGHTLY_USER_BATCH_SIZE):
                chunk = uids[i : i + _NIGHTLY_USER_BATCH_SIZE]
                count_rows = (
                    await db.execute(
                        select(Conversation.user_id, func.count(Message.id))
                        .select_from(Message)
                        .join(Conversation, Message.conversation_id == Conversation.id)
                        .where(
                            Conversation.user_id.in_(chunk),
                            Conversation.kind != CRON_KIND,
                            Message.role == "user",
                            # status_interaction 行 role 也是 "user"；戳一戳风暴不等于五条真消息的反思素材。
                            Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                            Message.created_at >= utc_start,
                            Message.created_at < utc_end,
                        )
                        .group_by(Conversation.user_id),
                    )
                ).all()
                for uid, cnt in count_rows:
                    msg_counts[int(uid)] = int(cnt)

            for uid, reference_utc, target_date_str in candidates:
                if msg_counts.get(uid, 0) >= NIGHTLY_MIN_MESSAGES_TODAY:
                    eligible.append((uid, reference_utc, target_date_str))

    if not eligible:
        return

    results = await asyncio.gather(*(run_nightly_pipeline(uid, reference_utc) for uid, reference_utc, _ in eligible), return_exceptions=True)
    for (uid, _, target_date_str), result in zip(eligible, results, strict=True):
        if isinstance(result, Exception):
            logger.error("nightly_activity: tick failed", exc_info=result, extra={"user_id": uid})
            continue
        if result is True:
            _LAST_NIGHTLY_RUN[uid] = target_date_str


async def scheduler_loop() -> None:
    """以 SCHEDULER_INTERVAL_SECONDS 为周期的 cron tick 循环：单 tick 粒度——不支持分钟以下调度。按 deadline 对齐而非 tick 结束后固定 sleep——后者的实际周期是 60s + tick 耗时，误差逐轮累积；落后超过一整周期时丢弃错过的槽位，避免停摆恢复后连打。_tick() 未捕获的异常会冒泡导致 BackgroundTask 死亡，运维侧曝光度高（Task exited with error）——这是故意的：持久 bug 不该每 60 秒静默刷日志，而应显式崩溃以便修复；派发出去的自主 turn 与扫描各自在独立 task 里失败并落日志，不会终止循环。"""
    logger.info("Starting background cron scheduler loop.")
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    while True:
        begin_local_scope()
        await _tick()
        next_at += SCHEDULER_INTERVAL_SECONDS
        now = loop.time()
        if next_at <= now:
            next_at = now + SCHEDULER_INTERVAL_SECONDS
        await asyncio.sleep(next_at - now)


def start_scheduler() -> None:
    _SCHEDULER.start(scheduler_loop())


async def stop_scheduler() -> None:
    """取消 scheduler task 并等待其退出；await 防止晚到的 tick 在 dispatcher 已拆除后还触发自主 turn（那些 turn 会失去 emitter）。"""
    await _SCHEDULER.stop()
