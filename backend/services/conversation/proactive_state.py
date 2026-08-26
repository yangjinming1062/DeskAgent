import time
from dataclasses import dataclass
from enum import Enum

from components import safe_json_loads
from modules.companion import Persona
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class ProactiveState(str, Enum):
    """进程内的主动外联状态机（per-user, 模块级字典持有）。
    IDLE / OUTREACHED / FOLLOWUP_SENT 各自承担不同的事件触发语义：

      - IDLE：未在主动外联周期内；新一轮 send_message_tool 转入 OUTREACHED。
      - OUTREACHED：LLM 通过 send_message_tool 发出了主动消息，等候用户响应或第一轮跟进 turn。
      - FOLLOWUP_SENT：cron 已触发过跟进 turn；LLM 仍在节奏内（可在 turn 中再次主动发言）。
        LLM 在跟进 turn 内再次 send_message_tool 时保持该状态并刷新 timestamp，等待下一轮 cron。

    LLM 给出 followup_timeout_seconds=0/None 表示「这一轮主动节奏到此为止」，状态回到 IDLE；
    用户发消息或档位切入静止同样重置回 IDLE。状态机不持久化，进程重启后回到 IDLE。
    """

    IDLE = "idle"
    OUTREACHED = "outreached"
    FOLLOWUP_SENT = "followup_sent"


@dataclass
class UserProactiveRecord:
    state: ProactiveState = ProactiveState.IDLE
    last_outreach_ts: float = 0.0
    last_proactive_text: str = ""
    # 0 表示「无下一次跟进」：LLM 主动给正数表示「我期望这么多秒内用户响应或我再次主动」，
    # 给 0 / None 表示「这一轮主动节奏到此为止」，状态机据此回到 IDLE。
    followup_timeout_seconds: float = 0.0
    # 用户最近一次与伙伴互动（发消息 / 戳摸）的单调时间戳；0 = 进程启动以来尚未互动。
    # 常规档的被冷落情绪反应据此计算「用户多久没理伙伴」。
    last_user_contact_ts: float = 0.0


_USER_PROACTIVE_STATE: dict[int, UserProactiveRecord] = {}


def _get_or_create_rec(user_id: int) -> UserProactiveRecord:
    return _USER_PROACTIVE_STATE.setdefault(user_id, UserProactiveRecord())


async def get_personality_tags(db: AsyncSession, user_id: int) -> list[str]:
    """读 Persona 表的 personality_tags_json；不存在或解析失败返回空列表。"""
    raw = (await db.execute(select(Persona.personality_tags_json).where(Persona.user_id == user_id))).scalar()
    if not raw:
        return []
    parsed = safe_json_loads(raw, default=[])
    return [t for t in parsed if isinstance(t, str) and t] if isinstance(parsed, list) else []


def record_user_outreach(
    user_id: int,
    text: str,
    followup_timeout_seconds: float | None = None,
) -> None:
    """记录 LLM 主动外联事件，并把状态机推入正确的下一态。

    行为契约：
      - IDLE 是"从未主动外联"的初始态：首次 send_message_tool 无条件进入 OUTREACHED，
        即使 LLM 没给 timeout（首次发送语义上当然要等用户响应或后续跟进）。
      - 非 IDLE + timeout=None 或 ≤0：LLM 显式结束本轮主动节奏 → 回到 IDLE。
      - 非 IDLE + 正数 timeout：进入或保持 FOLLOWUP_SENT，等候下一轮 cron 触发
        （OUTREACHED / FOLLOWUP_SENT 一律收敛到 FOLLOWUP_SENT）。
    """
    rec = _get_or_create_rec(user_id)
    no_timeout = followup_timeout_seconds is None or followup_timeout_seconds <= 0
    if rec.state == ProactiveState.IDLE:
        rec.state = ProactiveState.OUTREACHED
    elif no_timeout:
        rec.state = ProactiveState.IDLE
    else:
        rec.state = ProactiveState.FOLLOWUP_SENT

    rec.last_outreach_ts = time.monotonic()
    rec.last_proactive_text = text
    if followup_timeout_seconds is not None:
        rec.followup_timeout_seconds = max(0.0, followup_timeout_seconds)


def reset_user_outreach(user_id: int) -> None:
    """用户发消息或档位切入静止时把状态重置回 IDLE，终结跟进节奏。"""
    _USER_PROACTIVE_STATE[user_id] = UserProactiveRecord(state=ProactiveState.IDLE, last_outreach_ts=0.0, last_proactive_text="")


def note_user_contact(user_id: int) -> None:
    """用户侧互动（发消息 / 戳摸）时刷新接触时间戳——被冷落反应的计时起点。"""
    _get_or_create_rec(user_id).last_user_contact_ts = time.monotonic()


def get_user_proactive_record(user_id: int) -> UserProactiveRecord:
    """获取用户当前的主动跟踪记录；不存在则返回默认 IDLE 记录。"""
    return _USER_PROACTIVE_STATE.get(user_id, UserProactiveRecord())
