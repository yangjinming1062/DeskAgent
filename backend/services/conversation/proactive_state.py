import time
from dataclasses import dataclass
from enum import Enum


class ProactiveState(str, Enum):
    IDLE = "idle"
    OUTREACHED = "outreached"
    FOLLOWUP_SENT = "followup_sent"
    SUPPRESSED = "suppressed"


@dataclass
class UserProactiveRecord:
    state: ProactiveState = ProactiveState.IDLE
    last_outreach_ts: float = 0.0
    last_proactive_text: str = ""
    # 0.0 means "no follow-up" — the LLM sets a positive deadline only when it
    # actually wants to reach out again if the user stays silent.
    followup_timeout_seconds: float = 0.0


_USER_PROACTIVE_STATE: dict[int, UserProactiveRecord] = {}


def record_user_outreach(user_id: int, text: str, followup_timeout_seconds: float | None = None) -> None:
    """记录发送给用户的主动消息。"""
    rec = _USER_PROACTIVE_STATE.setdefault(user_id, UserProactiveRecord())
    if rec.state == ProactiveState.OUTREACHED:
        rec.state = ProactiveState.FOLLOWUP_SENT
    elif rec.state in (ProactiveState.FOLLOWUP_SENT, ProactiveState.SUPPRESSED):
        rec.state = ProactiveState.SUPPRESSED
    else:
        rec.state = ProactiveState.OUTREACHED
    rec.last_outreach_ts = time.monotonic()
    rec.last_proactive_text = text
    if followup_timeout_seconds is not None:
        rec.followup_timeout_seconds = max(0.0, followup_timeout_seconds)


def reset_user_outreach(user_id: int) -> None:
    """用户发消息时把跟进状态重置回 IDLE。"""
    _USER_PROACTIVE_STATE[user_id] = UserProactiveRecord(state=ProactiveState.IDLE, last_outreach_ts=0.0, last_proactive_text="")


def get_user_proactive_record(user_id: int) -> UserProactiveRecord:
    """获取用户当前的主动跟踪记录。"""
    return _USER_PROACTIVE_STATE.get(user_id, UserProactiveRecord())
