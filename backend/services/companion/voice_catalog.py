import base64

from components import MAX_VOICE_DESIGN_PROMPT_CHARS
from pydantic import BaseModel
from services.llm import resolve as resolve_provider_class
from services.llm import resolve_provider_chain
from services.llm import ServiceType
from services.llm import try_resolve
from services.llm import VoiceDesignResult
from sqlalchemy.orm import Session

_LANG_KEYWORDS: dict[str, list[str]] = {
    "zh": ["中文", "普通话", "国语", "chinese", "mandarin"],
    "en": ["英文", "英语", "english"],
}


class VoiceEntry(BaseModel):
    id: str
    label: str
    gender: str  # "female" | "male" | "neutral"
    language: str = ""  # "zh" | "en" | "multi" | ""
    tags: list[str]
    description: str = ""


DEFAULT_VOICE = VoiceEntry(id="", label="默认音色", gender="neutral", tags=["默认"], description="使用引擎默认音色。")


def active_tts_provider(db: Session, user_id: int) -> str:
    chain = resolve_provider_chain(db, user_id, "tts")
    return chain[0].provider_name if chain else ""


def _provider_class(provider_name: str):
    if not provider_name:
        return None
    return try_resolve(ServiceType.tts, provider_name)


def voices_for_provider(provider_name: str) -> list[VoiceEntry]:
    cls = _provider_class(provider_name)
    if cls is None:
        return []
    return [VoiceEntry(**v) for v in cls.VOICE_CATALOG]


def default_voice_id(provider_name: str) -> str:
    voices = voices_for_provider(provider_name)
    return voices[0].id if voices else ""


def list_voices(db: Session, user_id: int) -> dict:
    provider = active_tts_provider(db, user_id)
    cls = _provider_class(provider)
    guide = cls.VOICE_DESIGN_GUIDE if cls else None
    return {
        "provider": provider,
        "voices": [v.model_dump() for v in voices_for_provider(provider)],
        "supports_voice_design": guide is not None,
        "voice_design_guide": guide or "",
    }


def _score(preference: str, voice: VoiceEntry) -> int:
    p = preference.lower()
    score = 0
    for tag in voice.tags:
        t = tag.lower()
        if t in p:
            score += 2
        elif any(tok and tok in t for tok in p.split()):
            score += 1
    if voice.label.lower() in p:
        score += 2
    if voice.language and voice.language != "multi":
        kws = _LANG_KEYWORDS.get(voice.language, [])
        if any(kw.lower() in p for kw in kws):
            score += 2
    return score


def match_voice(preference: str, voices: list[VoiceEntry]) -> tuple[VoiceEntry, list[VoiceEntry]]:
    if not voices:
        return DEFAULT_VOICE, []
    ranked = sorted(voices, key=lambda v: _score(preference, v), reverse=True)
    best = ranked[0]
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


async def design_voice(db: Session, user_id: int, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
    if len(prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds {MAX_VOICE_DESIGN_PROMPT_CHARS} chars")
    chain = resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise ValueError("no TTS provider configured")
    config = chain[0]
    cls = resolve_provider_class(ServiceType.tts, config.provider_name)
    if cls.VOICE_DESIGN_GUIDE is None:
        raise ValueError(f"{config.provider_name} does not support voice design")
    provider = cls(config)
    return await provider.design_voice(prompt, preview_text=preview_text)
