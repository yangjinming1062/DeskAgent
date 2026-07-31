from pydantic import BaseModel
from services.llm import resolve_provider_chain
from sqlalchemy.orm import Session


class VoiceEntry(BaseModel):
    id: str
    label: str
    gender: str  # "female" | "male" | "neutral"
    tags: list[str]
    description: str = ""


# Curated, provider-aware TTS voice catalog. Voice ids are provider-specific
# (each cloud TTS engine names its voices differently); only ids verified to
# be accepted by the corresponding provider are listed, so a matched id never
# breaks synthesis. Providers with a single known-good voice still expose it
# so onboarding/settings always have *something* to pick — matching collapses
# to that one voice rather than failing.
_MIMO: list[VoiceEntry] = [
    VoiceEntry(id="mimo_default", label="默认音色", gender="neutral", tags=["默认", "温柔", "自然", "中性"], description="MiMo 默认音色，自然温和。"),
]

_MINIMAX: list[VoiceEntry] = [
    VoiceEntry(id="female-shaonv", label="少女音", gender="female", tags=["少女", "温柔", "甜", "活泼", "女"], description="清甜的少女音，活泼温柔。"),
    VoiceEntry(id="female-yujie", label="御姐音", gender="female", tags=["御姐", "清冷", "成熟", "沉稳", "女"], description="清冷成熟的御姐音。"),
    VoiceEntry(id="female-chengshu", label="知性女声", gender="female", tags=["知性", "温柔", "成熟", "女", "稳重"], description="温柔知性的成熟女声。"),
    VoiceEntry(id="female-mengyao", label="萌丫音", gender="female", tags=["萌", "可爱", "甜", "少女", "女"], description="软萌可爱的少女音。"),
    VoiceEntry(id="male-qn-qingse", label="青涩少年", gender="male", tags=["少年", "青涩", "清新", "男", "正太"], description="清新青涩的少年音。"),
    VoiceEntry(id="male-qn-jingying", label="精英男声", gender="male", tags=["精英", "沉稳", "成熟", "磁性", "男"], description="沉稳干练的精英男声。"),
]

_GEMINI: list[VoiceEntry] = [
    VoiceEntry(id="Kore", label="Kore", gender="neutral", tags=["温柔", "温暖", "自然", "中性"], description="温暖自然的音色。"),
    VoiceEntry(id="Puck", label="Puck", gender="neutral", tags=["活泼", "轻快", "俏皮"], description="轻快活泼的音色。"),
    VoiceEntry(id="Aoede", label="Aoede", gender="female", tags=["温柔", "柔和", "女", "轻"], description="柔和温婉的女声。"),
    VoiceEntry(id="Leda", label="Leda", gender="female", tags=["明亮", "青春", "女", "清"], description="明亮青春的女声。"),
    VoiceEntry(id="Charon", label="Charon", gender="male", tags=["低沉", "沉稳", "男", "磁性"], description="低沉稳重的男声。"),
    VoiceEntry(id="Fenrir", label="Fenrir", gender="male", tags=["果断", "有力", "男", "强势"], description="果断有力的男声。"),
]

_ZHIPU: list[VoiceEntry] = [
    VoiceEntry(id="tongtong", label="彤彤", gender="female", tags=["温柔", "自然", "女", "甜"], description="温柔自然的默认女声。"),
]

VOICE_CATALOG: dict[str, list[VoiceEntry]] = {
    "mimo": _MIMO,
    "minimax": _MINIMAX,
    "gemini": _GEMINI,
    "zhipu": _ZHIPU,
}

DEFAULT_VOICE = VoiceEntry(id="", label="默认音色", gender="neutral", tags=["默认"], description="使用引擎默认音色。")


def active_tts_provider(db: Session, user_id: int) -> str:
    chain = resolve_provider_chain(db, user_id, "tts")
    return chain[0].provider_name if chain else ""


def voices_for_provider(provider_name: str) -> list[VoiceEntry]:
    return VOICE_CATALOG.get(provider_name, [])


def list_voices(db: Session, user_id: int) -> dict:
    provider = active_tts_provider(db, user_id)
    return {"provider": provider, "voices": [v.model_dump() for v in voices_for_provider(provider)]}


def _score(preference: str, voice: VoiceEntry) -> int:
    p = preference.lower()
    score = 0
    for tag in voice.tags:
        t = tag.lower()
        if t in p:
            score += 2
        elif any(tok and tok in t for tok in p.split()):
            score += 1
    if voice.gender != "neutral" and voice.gender in p:
        score += 1
    if voice.label.lower() in p:
        score += 2
    return score


def match_voice(preference: str, voices: list[VoiceEntry]) -> tuple[VoiceEntry, list[VoiceEntry]]:
    if not voices:
        return DEFAULT_VOICE, []
    ranked = sorted(voices, key=lambda v: _score(preference, v), reverse=True)
    best = ranked[0]
    # When nothing matched, prefer a neutral default over an arbitrary top voice.
    if _score(preference, best) == 0:
        neutral = next((v for v in voices if v.gender == "neutral"), None)
        best = neutral or voices[0]
    alternatives = [v for v in ranked if v.id != best.id][:4]
    return best, alternatives


def match_user_voice(db: Session, user_id: int, preference: str) -> dict:
    provider = active_tts_provider(db, user_id)
    voices = voices_for_provider(provider)
    best, alternatives = match_voice(preference or "", voices)
    return {
        "provider": provider,
        "voice": best.model_dump(),
        "alternatives": [v.model_dump() for v in alternatives],
    }
