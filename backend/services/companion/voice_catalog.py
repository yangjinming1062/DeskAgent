from sqlalchemy.orm import Session

from services.llm import ServiceType, VoiceDesignResult, VoiceEntry, resolve, resolve_provider_chain, try_resolve, voices_for_provider

_LANG_KEYWORDS: dict[str, list[str]] = {"zh": ["中文", "普通话", "国语", "chinese", "mandarin"], "en": ["英文", "英语", "english"]}

# Tokens (whole-word, case-insensitive) that bias the matcher toward a gender.
# Avoid the old substring bug where "male" in "female voice" matched female.
_GENDER_KEYWORDS: dict[str, list[str]] = {"female": ["female", "女", "女声", "少女", "御姐", "女神"], "male": ["male", "男", "男声", "少年", "正太"]}


DEFAULT_VOICE = VoiceEntry(id="", label="默认音色", gender="neutral", tags=["默认"], description="使用引擎默认音色。")

# Stable sort: zh → multi → ∅ → en. Original order preserved within each bucket.
# Module-level so we don't re-allocate the dict on every ``list_tts_voices`` call.
_LANGUAGE_BUCKET: dict[str, int] = {"zh": 0, "multi": 1, "": 2, "en": 3}

# Derived from _LANGUAGE_BUCKET so the supported set can never drift from
# the sort order; unknown values fall through to the unfiltered catalog.
SUPPORTED_VOICE_LANGUAGES: frozenset[str] = frozenset(_LANGUAGE_BUCKET)


def normalize_voice_language(value: object) -> str | None:
    """Single null-rendering rule shared by REST + JSON-RPC list endpoints."""
    return value if isinstance(value, str) and value in SUPPORTED_VOICE_LANGUAGES else None


def active_tts_provider(db: Session, user_id: int) -> str:
    chain = resolve_provider_chain(db, user_id, "tts")
    return chain[0].provider_name if chain else ""


def _sort_voices_by_language(voices: list[VoiceEntry]) -> list[VoiceEntry]:
    return sorted(voices, key=lambda v: _LANGUAGE_BUCKET.get(v.language or "", 4))


def list_tts_voices(db: Session, user_id: int, language: str | None = None) -> dict:
    """Return voice catalog, optionally filtered by ``language`` (zh/en/multi/'').

    Filtering applies AFTER the language-aware sort so a zh-only view still
    leads with the curated zh ordering. ``default_voice`` falls back to the
    unfiltered DEFAULT_VOICE stub when the filter empties the catalog.
    """
    provider = active_tts_provider(db, user_id)
    cls = try_resolve(ServiceType.tts, provider) if provider else None
    guide = cls.VOICE_DESIGN_GUIDE if cls else None
    voices = _sort_voices_by_language(voices_for_provider(provider))
    if language:
        voices = [v for v in voices if v.language == language]
    return {
        "provider": provider,
        "voices": [v.model_dump() for v in voices],
        "default_voice": voices[0].model_dump() if voices else DEFAULT_VOICE.model_dump(),
        "supports_voice_design": guide is not None,
        "voice_design_guide": guide or "",
    }


def _score(preference: str, voice: VoiceEntry) -> int:
    p = preference.lower()
    p_tokens = p.split()
    score = 0
    for tag in voice.tags:
        t = tag.lower()
        if t in p:
            score += 2
        elif any(tok and tok in t for tok in p_tokens):
            score += 1
        # CJK preference blob won't token-split; substring-match both directions.
        elif any(tok and (tok in t or t in p) for tok in p_tokens if any("一" <= c <= "鿿" for c in tok)):
            score += 1
    if voice.label.lower() in p:
        score += 2
    if voice.language and voice.language != "multi":
        kws = _LANG_KEYWORDS.get(voice.language, [])
        if any(kw.lower() in p for kw in kws):
            score += 2
    # Token-level gender bias — avoids the old substring bug where
    # ``"male" in "female voice"`` returned True.
    if voice.gender in _GENDER_KEYWORDS:
        for kw in _GENDER_KEYWORDS[voice.gender]:
            if kw.lower() in p_tokens:
                score += 2
                break
            # CJK gender keyword; substring against the whole preference blob.
            if any("一" <= c <= "鿿" for c in kw) and kw.lower() in p:
                score += 2
                break
    return score


def match_voice(preference: str, voices: list[VoiceEntry]) -> tuple[VoiceEntry, list[VoiceEntry]]:
    if not voices:
        return DEFAULT_VOICE, []
    ranked = sorted(((_score(preference, v), v) for v in voices), key=lambda t: t[0], reverse=True)
    best_score, best = ranked[0]
    if best_score == 0:
        neutral = next((v for v in voices if v.gender == "neutral"), None)
        best = neutral or voices[0]
    alternatives = [v for _, v in ranked if v.id != best.id][:4]
    return best, alternatives


def match_user_voice(db: Session, user_id: int, preference: str) -> dict:
    provider = active_tts_provider(db, user_id)
    voices = voices_for_provider(provider)
    best, alternatives = match_voice(preference or "", voices)
    return {"provider": provider, "voice": best.model_dump(), "alternatives": [v.model_dump() for v in alternatives]}


async def design_voice(db: Session, user_id: int, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
    chain = resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise ValueError("no TTS provider configured")
    config = chain[0]
    cls = resolve(ServiceType.tts, config.provider_name)
    if cls.VOICE_DESIGN_GUIDE is None:
        raise ValueError(f"{config.provider_name} does not support voice design")
    provider = cls(config)
    return await provider.design_voice(prompt, preview_text=preview_text)
