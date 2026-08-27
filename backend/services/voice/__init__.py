"""服务端实时语音会话：全双工状态机（session）、回合编排（turn）、能量 VAD 与插话判别（vad）、子句切分（segmenter）、音频编解码（audio）。"""

from .session import ACTIVE_SESSIONS, VoiceSession, handle_voice_websocket

__all__ = ["ACTIVE_SESSIONS", "VoiceSession", "handle_voice_websocket"]
