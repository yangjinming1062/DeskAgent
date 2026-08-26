"""上行 PCM → WAV 容器包装与下行二进制音频帧编码。"""

import io
import struct
import wave

AUDIO_MAGIC = 0x53414131  # "SAA1"

# 下行帧头：小端 u32 magic / u8 flags(保留 0) / u8 encoding / u16 seg_index / u32 sample_rate / u32 payload_len。
_AUDIO_HEADER = struct.Struct("<IBBHII")

ENCODING_PCM_S16LE = 0
ENCODING_WAV = 1
ENCODING_MP3 = 2
ENCODING_OGG_OPUS = 3
ENCODING_AAC = 4
ENCODING_OTHER_CONTAINER = 255

_MIME_TO_ENCODING = {
    "audio/wav": ENCODING_WAV,
    "audio/x-wav": ENCODING_WAV,
    "audio/wave": ENCODING_WAV,
    "audio/mpeg": ENCODING_MP3,
    "audio/mp3": ENCODING_MP3,
    "audio/ogg": ENCODING_OGG_OPUS,
    "audio/opus": ENCODING_OGG_OPUS,
    "audio/mp4": ENCODING_AAC,
    "audio/aac": ENCODING_AAC,
    "audio/x-m4a": ENCODING_AAC,
}


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def encode_audio_frame(audio: bytes, mime: str, seg_index: int, sample_rate: int = 0) -> bytes:
    """容器编码（wav/mp3/ogg/aac）由客户端整体解码，采样率自容器读取；仅裸 PCM 依赖 header 的 sample_rate。"""
    encoding = _MIME_TO_ENCODING.get((mime or "").split(";")[0].strip().lower(), ENCODING_OTHER_CONTAINER)
    return _AUDIO_HEADER.pack(AUDIO_MAGIC, 0, encoding, seg_index, sample_rate, len(audio)) + audio
