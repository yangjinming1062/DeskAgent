"""为每位 onboarding 完成用户一次性生成 5 套系统预设对话（companion / developer / product_manager / copywriter / language_teacher）。幂等：已存在的预设跳过。"""

from datetime import UTC, datetime

from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.chat.prompt_presets import BUILTIN_PRESETS

from .main_conversation import HINT_TEXT, SPECIAL_KIND


async def ensure_system_conversations_for_user(db: AsyncSession, user_id: int) -> None:
    """确保该用户 5 套系统预设对话都存在；缺失补齐，不重建。幂等。"""
    existing = (
        (
            await db.execute(
                select(Conversation.system_preset_id).where(
                    Conversation.user_id == user_id,
                    Conversation.system_preset_id.is_not(None),
                ),
            )
        )
        .scalars()
        .all()
    )
    have = set(existing)

    for preset_id, preset in BUILTIN_PRESETS.items():
        if preset_id in have:
            continue
        try:
            async with db.begin_nested():
                conv = Conversation(
                    user_id=user_id,
                    kind=SPECIAL_KIND,
                    system_preset_id=preset_id,
                    title=preset.name,
                    is_deletable=False,
                    is_renamable=False,
                    updated_at=datetime.now(UTC),
                )
                db.add(conv)
                await db.flush()
                if preset_id == "companion":
                    db.add(
                        Message(
                            conversation_id=conv.id,
                            role="system",
                            content=HINT_TEXT,
                            subtype="hint",
                        ),
                    )
        except IntegrityError:
            continue

    await db.commit()
