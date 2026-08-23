import time
from dataclasses import dataclass
from enum import Enum

from components import safe_json_loads
from modules.companion import Persona
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class ProactiveState(str, Enum):
    """进程内的主动外联状态机（per-user, 模块级字典持有）。
    IDLE / OUTREACHED / FOLLOWUP_SENT / SUPPRESSED 各自承担不同的事件触发语义：

      - IDLE：未在主动外联周期内；新一轮 send_message_tool 转入 OUTREACHED。
      - OUTREACHED：LLM 通过 send_message_tool 发出了主动消息，等候用户响应或第一轮跟进 turn。
      - FOLLOWUP_SENT：cron 已触发过跟进 turn；LLM 仍在节奏内（可在 turn 中再次主动发言）。
        LLM 在跟进 turn 内再次 send_message_tool 时保持该状态并刷新 timestamp，等待下一轮 cron。
      - SUPPRESSED：主动外联被显式抑制（LLM 给出 followup_timeout_seconds=None/0，
        或档位回调在 quiet 档位下打断进行中的 OUTREACHED/FOLLOWUP_SENT）。
        此时段由 cron 在「保持安静档位持续 1 小时以上 + 角色性格」条件下注入小情绪反馈 turn，
        反馈本身走完整 send_message_tool 流程，与主动外联独立。

    状态机本身不持久化；进程重启后回到 IDLE。重启后需要重新累积安静时长才能触发小情绪。
    """

    IDLE = "idle"
    OUTREACHED = "outreached"
    FOLLOWUP_SENT = "followup_sent"
    SUPPRESSED = "suppressed"


@dataclass
class UserProactiveRecord:
    state: ProactiveState = ProactiveState.IDLE
    last_outreach_ts: float = 0.0
    last_proactive_text: str = ""
    # 0 表示「无下一次跟进」：LLM 主动给正数表示「我期望这么多秒内用户响应或我再次主动」，
    # 给 0 / None 表示「这一轮主动节奏到此为止」，状态机据此推入 SUPPRESSED。
    followup_timeout_seconds: float = 0.0
    # 用户进入"保持安静"档位的单调时间戳（0 = 未在安静档位）。
    # 由档位变更回调写入；cron 的小情绪通道据此计算"持续安静"时长。
    quiet_since_ts: float = 0.0


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
      - 非 IDLE + timeout=None 或 ≤0：LLM 显式结束本轮主动节奏 → SUPPRESSED。
      - 非 IDLE + 正数 timeout：进入或保持 FOLLOWUP_SENT，等候下一轮 cron 触发
        （OUTREACHED / SUPPRESSED / FOLLOWUP_SENT 一律收敛到 FOLLOWUP_SENT）。
    """
    rec = _get_or_create_rec(user_id)
    no_timeout = followup_timeout_seconds is None or followup_timeout_seconds <= 0
    if rec.state == ProactiveState.IDLE:
        rec.state = ProactiveState.OUTREACHED
    elif no_timeout:
        rec.state = ProactiveState.SUPPRESSED
    else:
        rec.state = ProactiveState.FOLLOWUP_SENT

    rec.last_outreach_ts = time.monotonic()
    rec.last_proactive_text = text
    if followup_timeout_seconds is not None:
        rec.followup_timeout_seconds = max(0.0, followup_timeout_seconds)


def reset_user_outreach(user_id: int) -> None:
    """用户发消息时把跟进状态重置回 IDLE；用户响应同时解抑制 SUPPRESSED。"""
    _USER_PROACTIVE_STATE[user_id] = UserProactiveRecord(state=ProactiveState.IDLE, last_outreach_ts=0.0, last_proactive_text="")


def get_user_proactive_record(user_id: int) -> UserProactiveRecord:
    """获取用户当前的主动跟踪记录；不存在则返回默认 IDLE 记录。"""
    return _USER_PROACTIVE_STATE.get(user_id, UserProactiveRecord())


def set_user_quiet_since(user_id: int, ts: float) -> None:
    """档位变更为「保持安静」时由调用方写入进入时间戳；ts=0 表示离开安静档位。

    不修改 state 字段——档位与主动状态机解耦：用户在安静档位时既可能处于 SUPPRESSED
    （之前被抑制）也可能处于 IDLE（安静档位但还没主动过），两个字段独立持有。
    """
    _get_or_create_rec(user_id).quiet_since_ts = ts


def get_user_quiet_duration(user_id: int, now: float) -> float:
    """用户已保持安静多少秒；未在安静档位返回 0。"""
    rec = _USER_PROACTIVE_STATE.get(user_id)
    if rec is None or rec.quiet_since_ts == 0:
        return 0.0
    return max(0.0, now - rec.quiet_since_ts)
