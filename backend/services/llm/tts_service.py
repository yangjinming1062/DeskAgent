"""TTS 供应商链调用：REST 合成端点共用的服务层。"""

from collections.abc import AsyncIterator

from components import SESSION_LOCAL

from .llm_client import MissingLlmConfigError, resolve_provider_chain
from .llm_fallback import execute_stream_with_fallback, execute_with_fallback
from .providers.base import AudioChunk, TTSResult
from .voice_catalog import pick_voice_id


async def synthesize_speech(user_id: int, text: str, voice: str = "") -> TTSResult:
    """走供应商链合成整段语音；链解析为空抛 MissingLlmConfigError。返回体含实际使用的音色 id。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise MissingLlmConfigError()
    return await execute_with_fallback(
        db=None,
        user_id=user_id,
        service_type="tts",
        call_fn=lambda p: p.synthesize(text, voice=pick_voice_id(voice, p.provider_name)),
        _chain=chain,
    )


async def synthesize_speech_stream(user_id: int, text: str, voice: str = "") -> AsyncIterator[AudioChunk]:
    """走供应商链流式合成；回退仅发生在首块之前（execute_stream_with_fallback 语义）。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise MissingLlmConfigError()
    async for chunk in execute_stream_with_fallback(
        db=None,
        user_id=user_id,
        service_type="tts",
        open_fn=lambda p: p.synthesize_stream(text, voice=pick_voice_id(voice, p.provider_name)),
        _chain=chain,
    ):
        yield chunk
