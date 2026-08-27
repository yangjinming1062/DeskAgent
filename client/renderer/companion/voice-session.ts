import type { ChatMediaItem } from '@/shared/types/spiritagent'

import { decodeVoiceAudioFrame, VOICE_OPS, VOICE_SAMPLE_RATE, type VoiceAudioSegment } from './voice-protocol'

export type VoiceSessionStatus = 'connecting' | 'ready' | 'reconnecting' | 'closed'

export interface VoiceTurnEndPayload {
  interrupted: boolean
  text: string
  affect?: { emotion?: string; actions?: string[]; locale?: string; target?: string } | null
  media?: ChatMediaItem[]
  usage?: Record<string, unknown> | null
}

export interface VoiceSessionCallbacks {
  onStatus(status: VoiceSessionStatus, message?: string): void
  onReady?(caps: { ttsStream: boolean }): void
  onAsrFinal(text: string): void
  onLlmStart(): void
  onTtsSegment(segIndex: number, text: string, segment: VoiceAudioSegment): void
  onTurnEnd(payload: VoiceTurnEndPayload): void
  onTurnError(stage: string, code: string, message: string): void
  onInterrupted(): void
}

const RECONNECT_MAX_ATTEMPTS = 2
const RECONNECT_BACKOFF_MS = 1500

// 语音会话 WS 客户端（/api/voice/ws，PROTOCOL §1.7）：文本控制帧 + 二进制音频帧。
// 意外掉线在窗口内自动重连（现铸 ticket + 重发 session.start）；进行中的回合随连接
// 丢失作废，用户再说一句即开新回合。
export class VoiceSessionClient {
  private ws: WebSocket | null = null
  private manualClose = false
  private reconnectAttempts = 0
  private readyResolve: ((value: void) => void) | null = null
  private pendingSegmentText: string | null = null

  private constructor(
    private readonly sessionId: string,
    private readonly voice: string,
    private readonly cb: VoiceSessionCallbacks
  ) {}

  static async open(sessionId: string, voice: string, cb: VoiceSessionCallbacks): Promise<VoiceSessionClient> {
    const client = new VoiceSessionClient(sessionId, voice, cb)
    await client.connect()

    return client
  }

  private async connect(): Promise<void> {
    this.cb.onStatus(this.reconnectAttempts > 0 ? 'reconnecting' : 'connecting')

    const url = await window.spiritagent.getVoiceWsUrl()
    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          op: VOICE_OPS.sessionStart,
          duplex: true,
          sample_rate: VOICE_SAMPLE_RATE,
          session_id: this.sessionId,
          ...(this.voice ? { voice: this.voice } : {})
        })
      )
    }

    ws.onmessage = (event): void => {
      if (typeof event.data === 'string') {
        this.handleText(event.data)
      } else {
        this.handleBinary(event.data as ArrayBuffer)
      }
    }

    ws.onclose = () => {
      this.ws = null

      if (this.manualClose) {
        this.cb.onStatus('closed')

        return
      }

      if (this.reconnectAttempts < RECONNECT_MAX_ATTEMPTS) {
        this.reconnectAttempts += 1
        setTimeout(() => {
          if (!this.manualClose) {
            void this.connect().catch(err => {
              this.cb.onStatus('closed', err instanceof Error ? err.message : String(err))
            })
          }
        }, RECONNECT_BACKOFF_MS)

        return
      }

      this.cb.onStatus('closed', '语音连接已断开')
    }

    return await new Promise<void>(resolve => {
      this.readyResolve = resolve
    })
  }

  private handleText(raw: string): void {
    let msg: Record<string, unknown>

    try {
      msg = JSON.parse(raw) as Record<string, unknown>
    } catch {
      return
    }

    const op = typeof msg.op === 'string' ? msg.op : ''

    switch (op) {
      case VOICE_OPS.sessionReady: {
        this.reconnectAttempts = 0
        this.cb.onStatus('ready')
        const capsRaw = msg.caps as Record<string, unknown> | undefined
        this.cb.onReady?.({ ttsStream: capsRaw?.tts_stream === true })
        this.readyResolve?.()
        this.readyResolve = null

        break
      }

      case VOICE_OPS.sessionError: {
        const message = typeof msg.message === 'string' ? msg.message : '语音会话建立失败'
        this.manualClose = true
        this.readyResolve?.()
        this.readyResolve = null
        this.cb.onStatus('closed', message)

        break
      }

      case VOICE_OPS.sessionClosed:
        this.manualClose = true
        this.cb.onStatus('closed')

        break

      case VOICE_OPS.asrFinal:
        this.cb.onAsrFinal(typeof msg.text === 'string' ? msg.text : '')

        break

      case VOICE_OPS.llmStart:
        this.cb.onLlmStart()

        break

      case VOICE_OPS.ttsSegment:
        // 协议保证文本帧先于其音频帧到达；暂存文本随下一帧音频一并送出。
        this.pendingSegmentText = typeof msg.text === 'string' ? msg.text : ''

        break

      case VOICE_OPS.turnEnd:
        this.cb.onTurnEnd({
          interrupted: msg.interrupted === true,
          text: typeof msg.text === 'string' ? msg.text : '',
          affect: (msg.affect as VoiceTurnEndPayload['affect']) ?? null,
          media: Array.isArray(msg.media) ? (msg.media as ChatMediaItem[]) : [],
          usage: (msg.usage as VoiceTurnEndPayload['usage']) ?? null
        })

        break

      case VOICE_OPS.turnError:
        this.cb.onTurnError(
          typeof msg.stage === 'string' ? msg.stage : 'protocol',
          typeof msg.code === 'string' ? msg.code : 'failed',
          typeof msg.message === 'string' ? msg.message : '语音回合失败'
        )

        break

      case VOICE_OPS.sessionInterrupted:
        this.cb.onInterrupted()

        break

      default:
        break
    }
  }

  private handleBinary(data: ArrayBuffer): void {
    const segment = decodeVoiceAudioFrame(data)

    if (!segment) {
      this.pendingSegmentText = null

      return
    }

    this.cb.onTtsSegment(segment.segIndex, this.pendingSegmentText ?? '', segment)
    this.pendingSegmentText = null
  }

  private sendControl(op: string, extra: Record<string, unknown> = {}): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ op, ...extra }))
    }
  }

  sendInterrupt(): void {
    this.sendControl(VOICE_OPS.interrupt)
  }

  sendPcmChunk(pcm: Int16Array): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      // 运行时 Int16Array 视图就是合法 BufferSource；类型层的 ArrayBufferLike 联合
      // （postMessage 来源无法静态收窄）在此收口，不复制 3200B/块的热路径。
      this.ws.send(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength) as Uint8Array<ArrayBuffer>)
    }
  }

  close(): void {
    this.manualClose = true
    this.readyResolve?.()
    this.readyResolve = null
    this.sendControl(VOICE_OPS.sessionEnd, { reason: 'client_end' })

    if (this.ws) {
      this.ws.onclose = null
      this.ws.close()
      this.ws = null
    }

    this.cb.onStatus('closed')
  }
}
