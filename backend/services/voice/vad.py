"""服务端上行语音活动检测：能量 VAD 判起说/断句，回放期间判插话（区分真人语音与扬声器残余回声）。

纯 Python 能量法（100ms 帧粒度），无模型/原生依赖——单副本 web 进程无 GPU，能量法在该粒度上
的 CPU 成本可忽略；回声判别依靠「与下行音频的因果性」（致盲窗回声底估计），而非声学模型。
"""

import enum
import math
import time
from array import array
from collections.abc import Iterator

FRAME_MS = 100
# 下行静默超过该时长后再次发声视为"恢复播放"，重开致盲窗。
_DOWNLINK_GAP_S = 0.3
# 语音判定的绝对门限（int16 RMS）：冷启动期相对门限（噪声底未收敛）的下限锚点。真实麦
# 底噪常为十几到几十、语音量级为千位；无此锚点时环境底高于初值会让静默被判成持续说话。
_ABS_SPEECH_FLOOR = 120.0


class VadEvent(enum.Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    OVERFLOW = "overflow"


def frame_bytes_for(sample_rate: int) -> int:
    """单帧字节数（s16le mono）。"""
    return sample_rate * 2 * FRAME_MS // 1000


def _split_frames(buf: bytearray, pcm: bytes, chunk: int) -> Iterator[bytes]:
    """把任意长度 PCM 追加进累积缓冲，按固定帧长产出完整帧；残余留待下次。"""
    buf.extend(pcm)
    while len(buf) >= chunk:
        frame = bytes(buf[:chunk])
        del buf[:chunk]
        yield frame


def _rms(frame: bytes) -> float:
    samples = array("h", frame)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _track_floor(floor: float, rms: float) -> float:
    """自适应噪声底：降快升慢，单帧升幅钳 20%。没有钳制时环境底高于初值（真实麦底噪 rms
    常为十几到几十，初值 1.0）需要靠"疑似语音不抬底"守卫跳过——而那会让底永远爬不上去，
    静默被判成持续说话；钳制后底能在 1-2 秒内爬到环境位，语音/突发帧又带不飞底。"""
    if rms < floor:
        return max(1.0, floor * 0.6 + rms * 0.4)
    return min(floor * 0.95 + rms * 0.05, floor * 1.2)


class EnergyVad:
    """自适应噪声底 + 起说/断句时长确认。噪声底仅在静默期跟踪：疑似语音帧不抬底（否则门限追着
    语音跑），降得快、升得慢。"""

    def __init__(self, sample_rate: int, *, floor_ratio: float, onset_ms: int, endpoint_ms: int, max_seconds: int) -> None:
        self._chunk = frame_bytes_for(sample_rate)
        self._floor_ratio = floor_ratio
        self._onset_frames = max(1, onset_ms // FRAME_MS)
        self._endpoint_frames = max(1, endpoint_ms // FRAME_MS)
        self._max_frames = max(1, max_seconds * 1000 // FRAME_MS)
        self._frame_buf = bytearray()
        self._floor = 1.0
        self._speech = False
        self._run = 0
        self._speech_frames = 0

    def feed(self, pcm: bytes) -> list[VadEvent]:
        events: list[VadEvent] = []
        for frame in _split_frames(self._frame_buf, pcm, self._chunk):
            if (event := self._feed_frame(frame)) is not None:
                events.append(event)
        return events

    def force_speaking(self) -> None:
        """插话判真后由会话调用：外部已确认说话开始，直接置入说话态（跳过起说确认）。"""
        self._speech = True
        self._run = 0
        self._speech_frames = 0

    def _feed_frame(self, frame: bytes) -> VadEvent | None:
        rms = _rms(frame)
        above = rms > max(self._floor * self._floor_ratio, _ABS_SPEECH_FLOOR)
        if not above:
            # 低于门限的帧（含说话期间的词间静默）向环境底收敛——底噪跟踪只在静默帧进行，
            # 语音/突发帧不抬底；说话期也跟踪，否则状态机长时间停在 speaking 后底噪冻结失真。
            self._floor = _track_floor(self._floor, rms)
        if not self._speech:
            if not above:
                self._run = 0
                return None
            self._run += 1
            if self._run >= self._onset_frames:
                self._speech = True
                self._run = 0
                self._speech_frames = self._onset_frames
                return VadEvent.SPEECH_START
            return None
        self._speech_frames += 1
        if self._speech_frames > self._max_frames:
            self._speech = False
            self._run = 0
            return VadEvent.OVERFLOW
        if above:
            self._run = 0
        else:
            self._run += 1
            if self._run >= self._endpoint_frames:
                self._speech = False
                self._run = 0
                return VadEvent.SPEECH_END
        return None


class BargeInDetector:
    """回合下发音频期间的上行插话判别。

    服务端没有独立参考信号做真回声消除，唯一可区分回声与真人的信号是因果性：下行音频从静默
    恢复后的首个致盲窗内，人类来不及开口，上行能量 ≈ 回声 + 噪声——用窗口峰值更新回声底；
    判定需同时越过「环境噪声底 × floor_ratio」与「回声底 × echo_ratio」并持续 onset 时长。
    漏判由端点兜底（误判只损失延迟不锁死）：被压制的能量若延续到回合结束，会被常规 VAD 路径
    收成话语。
    """

    def __init__(self, sample_rate: int, *, floor_ratio: float, echo_ratio: float, onset_ms: int, deafen_ms: int) -> None:
        self._chunk = frame_bytes_for(sample_rate)
        self._floor_ratio = floor_ratio
        self._echo_ratio = echo_ratio
        self._onset_frames = max(1, onset_ms // FRAME_MS)
        self._deafen_frames = max(1, deafen_ms // FRAME_MS)
        self._frame_buf = bytearray()
        self._ambient_floor = 1.0
        self._echo_floor = 1.0
        self._deafen_left = 0
        self._run = 0
        self._last_note: float | None = None

    def on_downlink_audio(self) -> None:
        now = time.monotonic()
        prev, self._last_note = self._last_note, now
        if prev is None or now - prev > _DOWNLINK_GAP_S:
            self._deafen_left = self._deafen_frames

    def feed(self, pcm: bytes) -> bool:
        """喂上行 PCM；返回 True 表示判定为真人插话（调用方应立即取消回合）。"""
        for frame in _split_frames(self._frame_buf, pcm, self._chunk):
            rms = _rms(frame)
            self._ambient_floor = _track_floor(self._ambient_floor, rms)
            if self._deafen_left > 0:
                self._deafen_left -= 1
                # 慢降快升：回声变小后给更高的余量衰减，变大立即抬。
                self._echo_floor = max(self._echo_floor * 0.7, rms * 1.1)
                self._run = 0
                continue
            if rms > max(self._ambient_floor * self._floor_ratio, self._echo_floor * self._echo_ratio):
                self._run += 1
                if self._run >= self._onset_frames:
                    self._run = 0
                    return True
            else:
                self._run = 0
        return False

    def reset(self) -> None:
        """回合结束清计数；回声底保留（同房间同音量的下次估计不必从零开始）。"""
        self._frame_buf.clear()
        self._run = 0
        self._deafen_left = 0
        self._last_note = None
