import re
from typing import Any

from components import get_logger
from modules.companion import CompanionExpression
from sqlalchemy import select

# 内置基础情绪；未识别的 LLM token 兜底为 neutral，避免脏数据污染渲染端状态。
BUILTIN_EMOTIONS: frozenset[str] = frozenset(
    {
        "happy",
        "sad",
        "surprised",
        "excited",
        "confused",
        "concerned",
        "shy",
        "proud",
        "grateful",
        "playful",
        "bored",
        "neutral",
        "lonely",
        "sleepy",
        "curious",
        "embarrassed",
        "apologetic",
        "pout",
        "angry",
        "smug",
        "scared",
        "relieved",
    },
)

ALLOWED_LOCALES: frozenset[str] = frozenset({"home", "chat", "perch", "roam", "sleep"})

# 锚定缓冲区开头的 tag 正则；target 允许非 ]/非换行字符，以容纳本地化应用名（如「微信」）与空格（如「Visual Studio Code」）。
_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)\]\n?", re.IGNORECASE)
_SPATIAL_RE = re.compile(r"^\s*\[spatial:([a-z_]+)(?:,target:([^\]\n]+))?\]\n?", re.IGNORECASE)

# 部分匹配正则：flush() 时抢救流中断前的解析结果，避免把半截标签（如 [affect:foo）暴露给用户。
_PARTIAL_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)?", re.IGNORECASE)
_PARTIAL_SPATIAL_RE = re.compile(r"^\s*\[spatial:([a-z_]+)?(?:,target:([^\]\n]*))?", re.IGNORECASE)

# 星号包裹的第三人称动作旁白（不显示不朗读，仅由 [affect:...] 驱动 3D 反应）。
_ACTION_NARRATION_RE = re.compile(r"^\s*\*[^*]*\*\s*")
_PARTIAL_ACTION_RE = re.compile(r"^\s*\*[^*]*$")

# 结构化动作 tag：[action:slug] 由 LLM 命名具体肢体动作，客户端映射到动画 clip 并在缺失时回退到情绪 valence。
_ACTION_TAG_RE = re.compile(r"^\s*\[action:([a-z_]+)\]\n?", re.IGNORECASE)
_PARTIAL_ACTION_TAG_RE = re.compile(r"^\s*\[action:([a-z_]+)?", re.IGNORECASE)

# 合理上限：含长 app 名的真实 tag 远小于 256 字符；超出视为不可解析输入，由 scrubber 丢弃并以文本形式下传。
_MAX_TAG_LEN: int = 256

logger = get_logger(__name__)


async def resolve_allowed_emotions(db: Any, user_id: int | None = None) -> frozenset[str]:
    """返回 BUILTIN_EMOTIONS 与用户自定义 CompanionExpression 名称的并集。"""
    if user_id is None or db is None:
        return BUILTIN_EMOTIONS
    try:
        rows = (await db.execute(select(CompanionExpression.name).where(CompanionExpression.user_id == user_id))).all()
        if not rows:
            return BUILTIN_EMOTIONS
        return BUILTIN_EMOTIONS | frozenset(r[0] for r in rows if r[0])
    except Exception:
        logger.warning("resolve_allowed_emotions failed; falling back to builtins", exc_info=True)
        return BUILTIN_EMOTIONS


async def resolve_custom_expressions(db: Any, user_id: int | None = None) -> list[Any]:
    """resolve_allowed_emotions 与 prompt builder 共用的 CompanionExpression 查询。"""
    if user_id is None or db is None:
        return []
    try:
        return (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user_id))).scalars().all()
    except Exception:
        logger.warning("resolve_custom_expressions failed; returning empty list", exc_info=True)
        return []


def build_affect_guidance(custom_expressions: list[Any] | None = None, available_actions: list[str] | None = None) -> str:
    """构建 affect 引导 prompt：内置情绪 + 用户自定义情绪 + 可用动作动画。"""
    emotions_set = set(BUILTIN_EMOTIONS)
    custom_desc_lines: list[str] = []
    if custom_expressions:
        for expr in custom_expressions:
            name = getattr(expr, "name", "")
            label = getattr(expr, "label", "")
            desc = getattr(expr, "description", "")
            if name:
                emotions_set.add(name)
                if desc or label:
                    desc_str = f" ({label}: {desc})" if desc else f" ({label})"
                    custom_desc_lines.append(f"- {name}{desc_str}")

    action_list = ""
    if available_actions:
        action_list = "Available action animations — choose [action:...] from exactly these names: " + ", ".join(sorted(set(available_actions))) + ".\n"

    guidance = (
        "# Companion Affect & Embodied Movement\n"
        "You are a companion with a visible on-screen 3D avatar. EMOTION swaps your expression avatar image "
        "beside the chat and plays matching body-language animation on the 3D avatar; ACTION names a specific movement. "
        "To convey your emotion and autonomously control your physical position/movement, "
        "begin your text response with an affect tag and optional action/spatial tags on their own lines:\n"
        "    [affect:EMOTION]\n"
        "    [action:ACTION]  (optional; a specific movement in snake_case, e.g. turn_away / stomp / nod)\n"
        "    [spatial:LOCALE,target:KEYWORD]  (optional)\n"
        "followed by your actual reply. EMOTION must be one of: " + ", ".join(sorted(emotions_set)) + ".\n" + action_list + "Note on Consecutive Bubbles:\n"
        "- To send multiple short consecutive replies inside one turn (e.g. a quick answer followed by an afterthought), put a line containing only --- between consecutive replies; the desktop renders each segment as its own bubble with a brief pause. Do not overuse this.\n"
        + "Note on Silent / Body Language Responses:\n"
        "- To express purely through body language without speaking, output ONLY the [affect:EMOTION] tag (optionally with [action:ACTION]) and no text reply; the companion reacts with its expression avatar and body language.\n"
        "- If you accompany the body language with a spoken reply, put the reply text after the tags.\n"
    )
    if custom_desc_lines:
        guidance += "Custom emotion details:\n" + "\n".join(custom_desc_lines) + "\n"
    guidance += (
        "LOCALE must be one of: " + ", ".join(sorted(ALLOWED_LOCALES)) + ". KEYWORD is an active window or app name.\n"
        "Examples:\n"
        "    [affect:happy]\n"
        "    I'm glad to see you! What are we working on today?\n"
        "    [affect:curious]\n"
        "    [spatial:perch,target:bilibili]\n"
        "    That video looks interesting! I'll watch it together with you.\n"
        "The tags are stripped before the user sees your message, so never explain them."
    )
    return guidance


def _is_potential_prefix(buf: str) -> bool:
    """缓冲区可能仍是尚未到达的 tag 或动作旁白前缀。"""
    s = buf.lstrip()
    if s.startswith("[") and "]" not in s:
        return True
    return s.startswith("*") and "*" not in s[1:]


class AffectScrubber:
    """从 LLM 流中剥离开头的 ``[affect:...]``/``[spatial:...]`` 标记与星号动作旁白，并向外暴露解析值。"""

    def __init__(self, allowed_emotions: frozenset[str] | None = None) -> None:
        self._buf: str = ""
        self._emotion: str | None = None
        self._spatial_locale: str | None = None
        self._spatial_target: str | None = None
        self._action: str | None = None
        self._allowed: frozenset[str] = allowed_emotions if allowed_emotions is not None else BUILTIN_EMOTIONS

    @property
    def emotion(self) -> str | None:
        return self._emotion

    @property
    def action(self) -> str | None:
        return self._action

    @property
    def spatial_locale(self) -> str | None:
        return self._spatial_locale

    @property
    def spatial_target(self) -> str | None:
        return self._spatial_target

    def feed(self, text: str) -> str:
        if not text:
            return text
        self._buf += text
        return self._try_resolve()

    def flush(self) -> str:
        """流结束：再做一次完整匹配，再处理部分匹配。"""
        if not self._buf:
            return ""
        self._try_match_tags()
        m_aff = _PARTIAL_AFFECT_RE.match(self._buf)
        if m_aff:
            if m_aff.group(1):
                self._set_emotion(m_aff.group(1))
            self._consume(m_aff, strip_bracket=True)
        m_spat = _PARTIAL_SPATIAL_RE.match(self._buf)
        if m_spat:
            self._consume(m_spat, strip_bracket=True)
        m_act_tag = _PARTIAL_ACTION_TAG_RE.match(self._buf)
        if m_act_tag:
            if m_act_tag.group(1):
                self._set_action(m_act_tag.group(1))
            self._consume(m_act_tag, strip_bracket=True)
        m_act = _PARTIAL_ACTION_RE.match(self._buf)
        if m_act:
            self._consume(m_act)
        out, self._buf = self._buf, ""
        return out

    def _try_resolve(self) -> str:
        self._try_match_tags()
        if _is_potential_prefix(self._buf) and len(self._buf) < _MAX_TAG_LEN:
            return ""
        out, self._buf = self._buf, ""
        return out

    def _try_match_tags(self) -> None:
        """就地消费 ``self._buf`` 开头的完整 tag 前缀。"""
        while True:
            m_aff = _AFFECT_RE.match(self._buf)
            if m_aff:
                self._set_emotion(m_aff.group(1))
                self._consume(m_aff)
                continue
            m_spat = _SPATIAL_RE.match(self._buf)
            if m_spat:
                self._set_spatial(m_spat.group(1), m_spat.group(2))
                self._consume(m_spat)
                continue
            m_act_tag = _ACTION_TAG_RE.match(self._buf)
            if m_act_tag:
                self._set_action(m_act_tag.group(1))
                self._consume(m_act_tag)
                continue
            m_act = _ACTION_NARRATION_RE.match(self._buf)
            if m_act:
                self._consume(m_act)
                continue
            return

    def _set_emotion(self, token: str | None) -> None:
        if token is None:
            return
        normalized = token.lower()
        self._emotion = normalized if normalized in self._allowed else "neutral"

    def _set_spatial(self, loc: str | None, target: str | None) -> None:
        if loc is None:
            return
        normalized = loc.lower()
        self._spatial_locale = normalized if normalized in ALLOWED_LOCALES else None
        self._spatial_target = target

    def _set_action(self, token: str | None) -> None:
        if token is None:
            return
        self._action = token.lower()

    def _consume(self, m: re.Match[str], *, strip_bracket: bool = False) -> None:
        """将 ``self._buf`` 推进到匹配之后；可选地吃掉部分正则残留的尾随 ``]``。"""
        self._buf = self._buf[m.end() :]
        if strip_bracket and self._buf.startswith("]"):
            self._buf = self._buf[1:]
