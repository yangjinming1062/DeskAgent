import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Any

from components import (
    MEMORY_CONSOLIDATE_INTERVAL_SECONDS,
    MEMORY_CONSOLIDATE_TRIGGER_ROWS,
    NIGHTLY_MIN_MESSAGES_TODAY,
    NIGHTLY_SCAN_INTERVAL_SECONDS,
    NIGHTLY_WINDOW_END_HOUR,
    NIGHTLY_WINDOW_START_HOUR,
    SETTINGS,
    BackgroundTask,
    begin_local_scope,
    get_logger,
    session_scope,
    utc_now,
)
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import CronJob
from modules.ws import WSEvent
from sqlalchemy import func, select, text
from sqlalchemy.engine import Row

from services.conversation import CRON_KIND, UI_ONLY_SUBTYPES, ProactiveState, get_user_proactive_record, record_user_outreach
from services.disturbance import is_quiet
from services.gateway.connection import MANAGER

from .cron_jobs import _compute_next_run_at
from .memory_consolidator import maybe_consolidate_one_user
from .nightly_activity import get_local_day_utc_bounds, run_nightly_pipeline

logger = get_logger(__name__)

_BG_TASKS: set[asyncio.Task] = set()


async def drain() -> None:
    """取消并 await 所有后台 task，容忍 CancelledError；由 main.py lifespan 关停时调用，避免 SIGTERM 在 db.commit() 中途被 engine 释放。"""
    if not _BG_TASKS:
        return
    pending = list(_BG_TASKS)
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


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

# 每个 tick 处理的到期 job 硬上限——限制批量 CAS 的语句大小和单 tick 工作量，避免长时间停摆后的回追（例如 60 分钟 ``* * * * *`` 调度，第一 tick 有 3600 个到期）。超出上限的 job 保留原 next_run_at，下一 tick 再触发。
_MAX_DUE_PER_TICK = 200


async def _select_due_jobs() -> list[Row]:
    """读取到期 job：只选 CAS + autonomous-turn kickoff 需要的列（去掉 deliver、created_at、updated_at、is_paused）；保留 prompt 因为 autonomous-turn kickoff 直接读它，而 CronJob.prompt 是 Text 列可能达 MB，节省是有意义的。ORDER BY next_run_at, id 在 _MAX_DUE_PER_TICK 截断积压时给出确定子集。"""
    now = utc_now()
    async with session_scope() as db:
        return (
            await db.execute(
                select(CronJob.id, CronJob.user_id, CronJob.name, CronJob.schedule, CronJob.next_run_at, CronJob.prompt, CronJob.one_shot)
                .where(CronJob.is_paused.is_(False), CronJob.next_run_at.is_not(None), CronJob.next_run_at <= now)
                .order_by(CronJob.next_run_at, CronJob.id)
                .limit(_MAX_DUE_PER_TICK + 1)
            )
        ).all()


async def _bulk_cas_advance(due_jobs: list[Row], now: datetime) -> dict[int, dict[str, Any]]:
    """批量 CAS 推进每个到期 job 的 next_run_at：CAS 谓词 (id, next_run_at, schedule) 防止 update_job 在 tick 中途推进 next_run_at（不匹配的行静默落败被丢弃）；用行值 IN 让 recurring UPDATE / one-shot DELETE 各一次语句搞定（PG 和 SQLite ≥ 3.15 都支持），避免最多 200 次串行往返。返回 {job_id: {user_id, is_paused, payload}} 给 CAS 胜者，RETURNING 里没出现的视为落败丢弃。
    db.commit() 必须显式：utils.session_scope 只 auto-close 不 commit；不显式 commit 时 db.close() 结束未提交事务，SQLAlchemy 在连接归还时丢弃 UPDATE。测试能巧合通过是因为 conftest 把一条连接裹在外层事务里，session_scope 写入跨调用可见但 commit 清理要等测试拆除时才跑。"""
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
            # PG：CAST 让 asyncpg 拿到 CASE 分支里的参数类型（它无法从赋值目标推断），列类型是 timestamptz 所以 cast 必须匹配。SQLite：不写 CAST——其 CAST 会套 NUMERIC 亲和截断 ISO 串。
            is_pg = db.bind is not None and db.bind.dialect.name == "postgresql"
            then = "CAST(:next_{i} AS timestamptz)" if is_pg else ":next_{i}"
            next_case = " ".join(f"WHEN :id_{i} THEN {then.format(i=i)}" for i in range(len(recurring)))
            match = ", ".join(f"(:id_{i}, :old_{i}, :sched_{i})" for i in range(len(recurring)))
            params: dict[str, Any] = {}
            for i, job in enumerate(recurring):
                params |= {f"id_{i}": job.id, f"next_{i}": new_runs[job.id], f"old_{i}": job.next_run_at, f"sched_{i}": job.schedule}
            # is_paused 由同一 CASE 派生：croniter 耗尽（= None）是唯一会让 job 停摆的状态。
            res = await db.execute(
                text(
                    f"UPDATE cron_jobs SET next_run_at = CASE id {next_case} END, is_paused = (CASE id {next_case} END) IS NULL "
                    f"WHERE (id, next_run_at, schedule) IN ({match}) RETURNING id"
                ),
                params,
            )
            won.update(r[0] for r in res.all())
        if one_shots:
            # 触发后删除一次性 job，避免堆积；next_run_at 上的 CAS 谓词防止双重触发。
            match = ", ".join(f"(:oid_{i}, :oold_{i})" for i in range(len(one_shots)))
            params = {f"oid_{i}": job.id for i, job in enumerate(one_shots)} | {f"oold_{i}": job.next_run_at for i, job in enumerate(one_shots)}
            res = await db.execute(text(f"DELETE FROM cron_jobs WHERE (id, next_run_at) IN ({match}) RETURNING id"), params)
            won.update(r[0] for r in res.all())
        await db.commit()

    return {job.id: winners[job.id] for job in due_jobs if job.id in won}


async def _advance_due_jobs(due_jobs: list[Row], now: datetime) -> None:
    """Tx1（批量 CAS）+ 自主 chat turn kickoff：自主 turn 是真正产品路径——cron 是伙伴主动触达的基础设施；投递复用与用户消息相同的 message.complete / companion.message 流水线，让 LLM 可以调 send_message_tool 并受桌面端打扰档位门控（plan §4.2）。"""
    winners = await _bulk_cas_advance(due_jobs, now)
    for job_id, meta in winners.items():
        if meta.get("is_paused"):
            continue
        try:
            t = asyncio.create_task(_kick_autonomous_turn(job_id, meta))
            _BG_TASKS.add(t)
            t.add_done_callback(_BG_TASKS.discard)
        except RuntimeError:
            # 没有运行中的 loop——跳过本 tick；job 的 next_run_at 已推进，下一 tick 会再拾起。
            logger.warning("cron: no running loop, skipping autonomous turn", extra={"job_id": job_id})


async def _kick_autonomous_turn(job_id: int, meta: dict[str, Any]) -> None:
    """向持有该用户 WS 的 replica 申请自主 chat turn：turn 在连接所在进程内执行（流式 delta、tool future、runtime session 都是进程本地的），所以 tick 所在 replica 只写一条 ws_events（cron.turn.request）；outbox claim 循环（connection._process_events，按 local_user_ids() 过滤）拣起；用户在所有 replica 都离线则被 GC 收割。"""
    user_id = meta["user_id"]
    prompt = (meta["payload"].get("prompt") or "").strip()
    if not prompt:
        return

    if await is_quiet(user_id):
        # 静默模式抑制自主触达；先 gate 再写行。
        logger.debug("cron: user is quiet, skipping autonomous turn", extra={"user_id": user_id, "job_id": job_id})
        return

    async with session_scope() as db:
        db.add(WSEvent(user_id=user_id, event_type="cron.turn.request", payload=json.dumps({"job_id": job_id, "prompt": prompt}, ensure_ascii=False)))
        await db.commit()
    logger.info("cron: autonomous turn requested", extra={"user_id": user_id, "job_id": job_id})


async def _maybe_run_proactive_followups(now: datetime) -> None:
    """扫描未应答主动外联的用户，触发跟进自主 turn。"""
    cur_time = time.monotonic()
    online_uids = MANAGER.local_user_ids()
    for uid in online_uids:
        rec = get_user_proactive_record(uid)
        if rec.state == ProactiveState.OUTREACHED and rec.followup_timeout_seconds > 0 and (cur_time - rec.last_outreach_ts) >= rec.followup_timeout_seconds:
            if await is_quiet(uid):
                continue

            last_text = rec.last_proactive_text or "问候"
            waited_minutes = round(rec.followup_timeout_seconds / 60)
            prompt = (
                f"[环境感知：你在大约 {waited_minutes} 分钟前向用户主动发送了：“{last_text}”，但用户一直没有回复你。"
                "请根据你的人设性格（例如傲娇吐槽、轻微担心、自言自语或选择保持安静）决定是否要跟进。若要跟进发送消息，请调用 send_message_tool 工具，若决定不再打扰则直接结束回复。]"
            )
            # 把状态推进到 FOLLOWUP_SENT，防止再次触发
            record_user_outreach(uid, last_text)
            async with session_scope() as db:
                db.add(WSEvent(user_id=uid, event_type="cron.turn.request", payload=json.dumps({"job_id": -1, "prompt": prompt}, ensure_ascii=False)))
                await db.commit()
            logger.info("cron: proactive follow-up turn requested", extra={"user_id": uid, "last_outreach_text": last_text})


async def _tick() -> None:
    """为到期 job CAS 推进 next_run_at 并申请自主 turn：每个到期 job 写一条 cron.turn.request ws_events，持有该用户 WS 的 replica 通过 outbox 循环拣走并在本地运行 turn；无双触发风险——CAS UPDATE 串行化胜者，outbox DELETE..RETURNING 是单消费者。"""
    now = utc_now()
    # Memory consolidator 与 cron-job 派发独立——不能用 ``if not due_jobs`` gate，否则没有 cron job 的安装永远不会触发 consolidation。
    await _maybe_run_memory_consolidator(now)
    await _maybe_run_autonomous_activity(now)
    await _maybe_run_proactive_followups(now)
    due_jobs = await _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning("cron: tick over cap, deferred to next tick", extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK})
    due_jobs = due_jobs[:_MAX_DUE_PER_TICK]
    if not due_jobs:
        return
    await _advance_due_jobs(due_jobs, now)


async def _maybe_run_memory_consolidator(now: datetime) -> None:
    """为 recall pool 超阈值的用户跑 recall consolidator：外层扫描受 _CONSOLIDATE_SCAN_INTERVAL_SECONDS 节流；per-user 节流（MEMORY_CONSOLIDATE_INTERVAL_SECONDS）防同一用户反复合并；per-user 调用通过 asyncio.gather 并发，单 tick 只付最大 LLM 延迟而非总和。"""
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
    if not SETTINGS.nightly_activity_enabled:
        return

    global _LAST_NIGHTLY_SCAN
    if now.timestamp() - _LAST_NIGHTLY_SCAN < NIGHTLY_SCAN_INTERVAL_SECONDS:
        return
    _LAST_NIGHTLY_SCAN = now.timestamp()

    async with session_scope() as db:
        rows = (await db.execute(select(Memory.user_id, Memory.content).where(Memory.context == "user_profile:timezone"))).all()

        eligible: list[tuple[int, datetime, str]] = []
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
            except Exception:
                continue

            if not (NIGHTLY_WINDOW_START_HOUR <= user_local_dt.hour < NIGHTLY_WINDOW_END_HOUR):
                continue

            if _LAST_NIGHTLY_RUN.get(uid) == target_date_str:
                continue

            msg_count = (
                await db.execute(
                    select(func.count())
                    .select_from(Message)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.user_id == uid,
                        Conversation.kind != CRON_KIND,
                        Message.role == "user",
                        # status_interaction 行 role 也是 "user"；戳一戳风暴不等于五条真消息的反思素材。
                        Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                        Message.created_at >= utc_start,
                        Message.created_at < utc_end,
                    )
                )
            ).scalar_one()
            if msg_count < NIGHTLY_MIN_MESSAGES_TODAY:
                continue

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
    """以 SCHEDULER_INTERVAL_SECONDS 为周期的 cron tick 循环：单 tick 粒度——不支持分钟以下调度。_tick() 未捕获的异常会冒泡导致 BackgroundTask 死亡，运维侧曝光度高（Task exited with error）——这是故意的：持久 bug 不该每 60 秒静默刷日志，而应显式崩溃以便修复；_tick() 已逐 job try/except，单个坏 job 不会让循环终止。"""
    logger.info("Starting background cron scheduler loop.")
    while True:
        begin_local_scope()
        await _tick()
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


_SCHEDULER = BackgroundTask("scheduler.cron_loop")


def start_scheduler() -> None:
    """把 scheduler loop 作为后台 task spawn；stop_scheduler 在关停时取消。"""
    _SCHEDULER.start(scheduler_loop())


async def stop_scheduler() -> None:
    """取消 scheduler task 并等待其退出；await 防止晚到的 tick 在 dispatcher 已拆除后还触发自主 turn（那些 turn 会失去 emitter）。"""
    await _SCHEDULER.stop()
