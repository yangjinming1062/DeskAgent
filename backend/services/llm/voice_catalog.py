from modules.companion import VoiceEntry

from .providers import ServiceType, TTSProvider, try_resolve


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
    # 优先选中性音色 —— 否则未匹配偏好时会锁到第一个带性别的条目。
    voices = voices_for_provider(provider_name)
    for v in voices:
        if v.gender == "neutral":
            return v.id
    return voices[0].id if voices else ""


def pick_voice_id(voice: str, provider_name: str) -> str:
    # voice id 是供应商私有的：仅在供应商自有 catalog 中才透传；否则回退到该供应商默认，避免外部 id 进入 ``synthesize()`` 后 400。设计 token（如 ``mimo_voicedesign:<prompt>``）动态出现、不在 VOICE_CATALOG 中 —— 直接透传供 synthesize() 按前缀路由。
    if voice and voice.startswith("mimo_voicedesign:"):
        return voice
    if voice and voice in (v.id for v in voices_for_provider(provider_name)):
        return voice
    return default_voice_id(provider_name)
