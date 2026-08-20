import json

from components import SESSION_LOCAL
from modules.ws import WSEvent


async def emit_companion_affect(user_id: int, emotion: str) -> None:
    """推送纯情绪事件到桌面端：只切换 EMOTIONAL 状态，不弹气泡也不合成 TTS。"""
    async with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.affect", payload=json.dumps({"emotion": emotion}, ensure_ascii=False)))
        await db.commit()
