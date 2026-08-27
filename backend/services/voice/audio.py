"""上行 PCM → WAV 容器包装与下行二进制音频帧编码。"""

import io
import struct
import wave

AUDIO_MAGIC = 0x53414131  # "SAA1"

# 段末块标志：同一 seg_index 的多个块共享序号，末块置位，客户端据此确认段落完成。
FLAG_SEG_FINAL = 0x01

# 下行帧头：小端 u32 magic / u8 flags / u8 encoding / u16 seg_index / u32 sample_rate / u32 payload_len。
_AUDIO_HEADER = struct.Struct("<IBBHII")

ENCODING_PCM_S16LE = 0
ENCODING_WAV = 1
ENCODING_MP3 = 2
ENCODING_OGG_OPUS = 3
ENCODING_AAC = 4
ENCODING_OTHER_CONTAINER = 255

_MIME_TO_ENCODING = {
    "audio/pcm": ENCODING_PCM_S16LE,
    "audio/l16": ENCODING_PCM_S16LE,
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


def encode_audio_frame(audio: bytes, mime: str, seg_index: int, sample_rate: int = 0, *, final: bool = False) -> bytes:
    """容器编码（wav/mp3/ogg/aac）由客户端整体解码，采样率自容器读取；仅裸 PCM 依赖 header 的 sample_rate。
    流式段拆多块下发时共享 seg_index，末块带 FLAG_SEG_FINAL；打断中途停止的段没有末块。"""
    encoding = _MIME_TO_ENCODING.get((mime or "").split(";")[0].strip().lower(), ENCODING_OTHER_CONTAINER)
    flags = FLAG_SEG_FINAL if final else 0
    return _AUDIO_HEADER.pack(AUDIO_MAGIC, flags, encoding, seg_index, sample_rate, len(audio)) + audio
