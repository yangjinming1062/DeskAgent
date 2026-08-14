from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import ProviderConfig

_RIG_TYPES: tuple[str, ...] = ("biped", "quadruped", "avian", "serpentine", "aquatic", "hexapod", "octopod")

_SYSTEM_PROMPT = (
    "你是一个 3D 骨骼类型分类助手。根据用户给出的物种，从下列 7 种骨骼类型中选最匹配的一种，只输出类型名本身，不要其他文字、不要标点、不要解释：\n"
    "biped：双足人形（人类、精灵、矮龙、人形机器人、猫娘等拟人化生物）；\n"
    "quadruped：四足动物（猫、狗、狼、马、鹿、兔子等）；\n"
    "avian：鸟类或有翼生物（鹰、凤凰、天使等）；\n"
    "serpentine：蛇形或龙形生物（蛇、龙等）；\n"
    "aquatic：鱼类或水生生物（鱼、海豚、人鱼等）；\n"
    "hexapod：六足生物（蚂蚁、甲虫等）；\n"
    "octopod：八足生物（蜘蛛、章鱼等）。"
)

_USER_TEMPLATE = "物种：{species}\n骨骼类型："


class _ChatFn(Protocol):
    async def __call__(self, db: AsyncSession | None, user_id: int | None, system_prompt: str, user_payload: str, *, provider_config: ProviderConfig | None = None) -> str: ...


async def select_rig_type(chat: _ChatFn, species: str, *, db: AsyncSession | None = None, user_id: int | None = None) -> str:
    """LLM chooses one of the 7 Tripo3D rig types based on species.

    Falls back to ``"biped"`` on any error or empty/invalid response — never raises,
    because a wrong rig type only changes animation library selection, not correctness.
    """
    try:
        species_text = species.strip() or "人类"
        user_payload = _USER_TEMPLATE.format(species=species_text)
        raw = await chat(db, user_id, _SYSTEM_PROMPT, user_payload)
        candidate = raw.strip().lower().split()[0].strip(".,;:!?`'\"") if raw else ""
        if candidate in _RIG_TYPES:
            return candidate
        return "biped"
    except Exception:
        return "biped"
