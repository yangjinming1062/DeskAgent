import json

from components import SESSION_LOCAL
from modules.ws import WSEvent


async def emit_companion_affect(user_id: int, emotion: str) -> None:
    """Push an affect-only cue (no message text, no TTS) to the user's desktop.

    Called by ``affect_check.check_affect`` when an idle-triggered LLM
    reasoning pass decides the companion should express a contextual emotion
    (§7.6: affect is memory-driven runtime behaviour). The desktop switches to
    EMOTIONAL(state) without popping a bubble or synthesising TTS — the
    ``companion.affect`` event type distinguishes "pure emotion" from
    ``companion.message``.
    """
    async with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.affect", payload=json.dumps({"emotion": emotion}, ensure_ascii=False)))
        await db.commit()
