from pydantic import BaseModel

from .providers import ServiceType
from .providers import try_resolve
from .providers import TTSProvider


class VoiceEntry(BaseModel):
    id: str
    label: str
    gender: str  # "female" | "male" | "neutral"
    language: str = ""  # "zh" | "en" | "multi" | ""
    tags: list[str]
    description: str = ""


def _provider_class(provider_name: str) -> type[TTSProvider] | None:
    if not provider_name:
        return None
    return try_resolve(ServiceType.tts, provider_name)


def voices_for_provider(provider_name: str) -> list[VoiceEntry]:
    cls = _provider_class(provider_name)
    if cls is None:
        return []
    return [VoiceEntry(**v) for v in cls.VOICE_CATALOG]


def default_voice_id(provider_name: str) -> str:
    # Prefer a neutral voice so a no-match preference doesn't lock onto the first gendered entry.
    voices = voices_for_provider(provider_name)
    for v in voices:
        if v.gender == "neutral":
            return v.id
    return voices[0].id if voices else ""


def pick_voice_id(voice: str, provider_name: str) -> str:
    # Voice ids are provider-private: only pass ``voice`` through to providers
    # that list it in their own catalog. Otherwise fall back to that provider's
    # default so a foreign id never reaches ``synthesize()`` and 400s.
    #
    # Design tokens (e.g. ``mimo_voicedesign:<prompt>``) are dynamic and never
    # appear in VOICE_CATALOG — pass them through so synthesize() can route by prefix.
    if voice and voice.startswith("mimo_voicedesign:"):
        return voice
    if voice and voice in (v.id for v in voices_for_provider(provider_name)):
        return voice
    return default_voice_id(provider_name)
