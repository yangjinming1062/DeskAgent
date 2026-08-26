// 语音会话 WS 协议（PROTOCOL §1.7）：控制帧 op + 下行二进制音频帧 16 字节头解码，
// 与 backend services/voice/audio.py 的布局对齐。

export const VOICE_SAMPLE_RATE = 16000
export const VOICE_AUDIO_MAGIC = 0x53414131 // "SAA1"

export const VOICE_OPS = {
  sessionStart: 'session.start',
  sessionReady: 'session.ready',
  sessionEnd: 'session.end',
  sessionClosed: 'session.closed',
  sessionError: 'session.error',
  utteranceStart: 'utterance.start',
  utteranceEnd: 'utterance.end',
  interrupt: 'interrupt',
  sessionInterrupted: 'session.interrupted',
  asrFinal: 'asr.final',
  asrSkipped: 'asr.skipped',
  llmStart: 'llm.start',
  ttsSegment: 'tts.segment',
  turnEnd: 'turn.end',
  turnError: 'turn.error'
} as const

// 编码值与 backend services/voice/audio.py 对齐；仅 PCM 依赖帧头的 sample_rate，
// 容器编码（wav/mp3/ogg/aac）由 decodeAudioData 自容器读取。
export const VOICE_ENCODING_PCM = 0

const AUDIO_HEADER_BYTES = 16

export interface VoiceAudioSegment {
  encoding: number
  sampleRate: number
  segIndex: number
  payload: ArrayBuffer
}

export function decodeVoiceAudioFrame(data: ArrayBuffer): VoiceAudioSegment | null {
  if (data.byteLength < AUDIO_HEADER_BYTES) {
    return null
  }

  const view = new DataView(data)

  if (view.getUint32(0, true) !== VOICE_AUDIO_MAGIC) {
    return null
  }

  const encoding = view.getUint8(5)
  const segIndex = view.getUint16(6, true)
  const sampleRate = view.getUint32(8, true)
  const payloadLen = view.getUint32(12, true)
  const payload = data.slice(AUDIO_HEADER_BYTES)

  // 打断竞速可能截断发送中的帧；载荷长度对不上时整帧丢弃。
  if (payloadLen !== payload.byteLength) {
    return null
  }

  return { encoding, sampleRate, segIndex, payload }
}
