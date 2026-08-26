import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $lastAssistantStreaming,
  setAssistantError,
  setChatOpen,
  setChatSession
} from '@/companion/chat-store'
import { $spriteState, setSpriteState } from '@/companion/companion-store'
import { usePanelDrag } from '@/companion/hooks/use-panel-drag'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { $subtitles, setSubtitles } from '@/companion/prefs'
import { $spatialPos, $spatialScale, $viewport, computeVoiceCallDockPosition } from '@/companion/spatial'
import { speak, stopSpeaking } from '@/companion/tts'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'
import { $gatewayState } from '@/shared/store/gateway'

import { SubtitlesOverlay } from './subtitles-overlay'

interface VoiceCallDockProps {
  onClose: () => void
}

const SPEECH_THRESHOLD = 28
const BARGEIN_THRESHOLD = 38
const SILENCE_END_MS = 1300
const WAVE_BARS = 24
// 在没有任何 message.start 到达时释放 awaiting-reply 锁，使麦克风能再次开启。
const AWAITING_REPLY_TIMEOUT_MS = 60_000

export const VOICE_CALL_AUDIO_CONSTRAINTS: MediaTrackConstraints = {
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  sampleRate: 16000
}

const PREFERRED_OPUS_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/ogg',
  'audio/mp4;codecs=opus',
  'audio/mp4'
] as const

export function getSupportedOpusMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return undefined
  }

  return PREFERRED_OPUS_MIME_TYPES.find(type => MediaRecorder.isTypeSupported(type))
}

export function getAudioExtensionForMime(mime: string): string {
  if (mime.includes('ogg')) {
    return 'ogg'
  }

  if (mime.includes('mp4')) {
    return 'mp4'
  }

  return 'webm'
}

// 嗅探后端/Runner 抛出的"忙/背压/限流"消息，用于给用户区别提示。
const BUSY_ERROR_PATTERN = /busy|backpressure|rate limit/i

export function isMediaBusyError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err)

  return BUSY_ERROR_PATTERN.test(msg)
}

// 实时半双工语音通话：麦克风持续录制，静音检测切片转写后提交 prompt，支持插话打断。
export function VoiceCallDock({ onClose }: VoiceCallDockProps): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
  const list = useStore($chatMessageList)
  const lastAssistantStreaming = useStore($lastAssistantStreaming)
  const spriteState = useStore($spriteState)
  const subtitlesVisible = useStore($subtitles)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const viewport = useStore($viewport)
  const [micActive, setMicActive] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const [durationSec, setDurationSec] = useState(0)
  const [waveform, setWaveform] = useState<number[]>(() => new Array(WAVE_BARS).fill(0))
  // 在 30ms tick 内复用同一份数组，仅在差异较大或周期性节流时推一次 state，
  // 避免 33Hz 全树重渲染（30ms cadence 是 VAD 需求，UI 不必同样密）。
  const waveformRef = useRef<number[]>(new Array(WAVE_BARS).fill(0))
  const tickCountRef = useRef(0)
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const durationSecRef = useRef(0)
  const vadIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const lastActivityTimeRef = useRef<number>(Date.now())
  const analyserRef = useRef<AnalyserNode | null>(null)
  const userSpeakingRef = useRef(false)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const awaitingReplyRef = useRef(false)
  const awaitingReplyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const assistantSpeakingRef = useRef(false)
  const lastSpokenIdRef = useRef<string | null>(null)
  // 新会话时重置 lastSpokenId 去重状态，避免首条回复被当作重复而跳过。
  const chatSessionId = useStore($chatSessionId)
  useEffect(() => {
    lastSpokenIdRef.current = null
  }, [chatSessionId])
  // 过期 speak() promise 不能在新一轮语音开始后把精灵拉回 idle。
  const speakGenRef = useRef(0)
  const gatewayStateRef = useLatestRef(gatewayState)
  const { requestGateway } = useGatewayRequest()
  const panelRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useLatestRef(onClose)

  useInteractiveRegion('voice-call-dock', panelRef)
  const { bind: dragBind, storedOffset } = usePanelDrag('da.companion.voiceCallOffset', () => panelRef.current)

  useEffect(() => {
    let unmounted = false
    let ctx: AudioContext | null = null
    navigator.mediaDevices
      ?.getUserMedia({ audio: VOICE_CALL_AUDIO_CONSTRAINTS })
      .then(stream => {
        if (unmounted) {
          stream.getTracks().forEach(track => track.stop())

          return
        }

        streamRef.current = stream
        setMicActive(true)
        setSpriteState('listening')

        try {
          const AudioContextClass = getAudioContextCtor()

          if (AudioContextClass) {
            ctx = new AudioContextClass()
            const source = ctx.createMediaStreamSource(stream)
            const analyser = ctx.createAnalyser()
            analyser.fftSize = 256
            source.connect(analyser)
            analyserRef.current = analyser
            const dataArray = new Uint8Array(analyser.frequencyBinCount)

            const startRecorder = () => {
              if (recorderRef.current?.state === 'recording') {
                return
              }

              try {
                const mimeType = getSupportedOpusMimeType()
                const rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
                const utteranceChunks: Blob[] = []

                rec.ondataavailable = e => {
                  if (e.data.size > 0) {
                    utteranceChunks.push(e.data)
                  }
                }

                rec.onstop = () => {
                  void transcribeAndSubmit(utteranceChunks, rec.mimeType || mimeType)
                }

                rec.start()
                recorderRef.current = rec
              } catch {
                /* recorder unavailable — skip this utterance */
              }
            }

            const finishUtterance = () => {
              const rec = recorderRef.current
              recorderRef.current = null

              if (!rec || rec.state !== 'recording') {
                userSpeakingRef.current = false

                return
              }

              rec.stop()
              userSpeakingRef.current = false
            }

            const checkVolume = () => {
              analyser.getByteFrequencyData(dataArray)
              const avg = dataArray.reduce((acc, val) => acc + val, 0) / dataArray.length

              if (avg > SPEECH_THRESHOLD || assistantSpeakingRef.current) {
                lastActivityTimeRef.current = Date.now()
              }

              // 打断：用户趁伙伴说话时开口。
              if (avg > BARGEIN_THRESHOLD && assistantSpeakingRef.current) {
                stopSpeaking()
                assistantSpeakingRef.current = false
                setSpriteState('listening')
              }

              const canListen = !awaitingReplyRef.current && !assistantSpeakingRef.current

              if (canListen) {
                if (!userSpeakingRef.current && avg > SPEECH_THRESHOLD) {
                  userSpeakingRef.current = true
                  setSpriteState('listening')
                  startRecorder()
                  clearTimeout(silenceTimerRef.current ?? undefined)
                  silenceTimerRef.current = null
                } else if (userSpeakingRef.current && avg < SPEECH_THRESHOLD) {
                  if (!silenceTimerRef.current) {
                    silenceTimerRef.current = setTimeout(() => {
                      silenceTimerRef.current = null

                      if (userSpeakingRef.current) {
                        finishUtterance()
                      }
                    }, SILENCE_END_MS)
                  }
                } else if (userSpeakingRef.current && avg >= SPEECH_THRESHOLD && silenceTimerRef.current) {
                  clearTimeout(silenceTimerRef.current)
                  silenceTimerRef.current = null
                }
              }
            }

            // 波形采样：从频域分桶抽取 WAVE_BARS 个高度值供 UI 绘制
            // （DESIGN §6.1「精灵带通话中光环与波形指示」）。
            // 复用同一个 waveform 数组减少 GC 压力；只有数值变化时才触发 setState。
            const sampleWaveform = () => {
              const next = waveformRef.current
              const step = Math.max(1, Math.floor(dataArray.length / WAVE_BARS))
              let changed = false

              for (let i = 0; i < WAVE_BARS; i++) {
                const start = i * step
                const end = Math.min(dataArray.length, start + step)
                let sum = 0

                for (let j = start; j < end; j++) {
                  sum += dataArray[j] ?? 0
                }

                const v = sum / Math.max(1, end - start)

                if (Math.abs((next[i] ?? 0) - v) > 0.5) {
                  next[i] = v
                  changed = true
                } else if (next[i] === undefined) {
                  next[i] = v
                  changed = true
                }
              }

              // 每 5 次采样（约 150ms）才推一次 React 状态——保证视觉刷新率足够，
              // 但减少 33Hz 全树重渲染。
              tickCountRef.current++

              if (changed && tickCountRef.current % 5 === 0) {
                setWaveform(next.slice())
              }
            }

            // 定时器驱动（30ms），避免窗口隐藏 / 遮挡时 requestAnimationFrame 被 Chromium 节流暂停导致语音识别失效。
            vadIntervalRef.current = setInterval(() => {
              checkVolume()
              sampleWaveform()
            }, 30)
          }
        } catch {
          /* AudioContext fallback — mic works, VAD disabled */
        }
      })
      .catch(() => {
        setMicError('无法接入麦克风，请检查系统权限')
        setSpriteState('idle')
      })

    timerRef.current = setInterval(() => {
      durationSecRef.current += 1
      setDurationSec(durationSecRef.current)

      const inactiveSec = Math.floor((Date.now() - lastActivityTimeRef.current) / 1000)

      if (inactiveSec >= 180) {
        onCloseRef.current()
      }
    }, 1000)

    async function transcribeAndSubmit(chunks: Blob[], mimeType?: string): Promise<void> {
      if (unmounted || !chunks.length) {
        return
      }

      const totalBytes = chunks.reduce((acc, c) => acc + c.size, 0)

      if (totalBytes === 0) {
        setSpriteState('listening')

        return
      }

      const selectedMime = mimeType || 'audio/webm'
      const blob = new Blob(chunks, { type: selectedMime })
      let text = ''

      try {
        const reader = new FileReader()

        const dataUrl: string = await new Promise((resolve, reject) => {
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => reject(new Error('read failed'))
          reader.readAsDataURL(blob)
        })

        const ext = getAudioExtensionForMime(selectedMime)
        const res = await window.spiritagent.media.stt({ dataUrl, filename: `voice.${ext}` })
        text = (res.text ?? '').trim()
      } catch (err: unknown) {
        if (!unmounted) {
          // 把 STT 失败暴露给用户，而不是悄悄回到倾听状态。
          setAssistantError(isMediaBusyError(err) ? '语音服务正忙，请稍候再试' : '没听清，请再说一次')
        }

        text = ''
      }

      if (unmounted) {
        return
      }

      if (!text || gatewayStateRef.current !== 'open') {
        setSpriteState('listening')

        return
      }

      awaitingReplyRef.current = true
      setSpriteState('thinking')

      // WS 在任何 message.start 到达前断开时恢复；正常完成时清除。
      if (awaitingReplyTimerRef.current) {
        clearTimeout(awaitingReplyTimerRef.current)
      }

      awaitingReplyTimerRef.current = setTimeout(() => {
        awaitingReplyTimerRef.current = null

        if (awaitingReplyRef.current) {
          awaitingReplyRef.current = false
          setSpriteState('listening')
        }
      }, AWAITING_REPLY_TIMEOUT_MS)

      try {
        const id = await ensureSession()
        await requestGateway('prompt.submit', { session_id: id, text })
      } catch {
        awaitingReplyRef.current = false

        if (awaitingReplyTimerRef.current) {
          clearTimeout(awaitingReplyTimerRef.current)
          awaitingReplyTimerRef.current = null
        }

        setSpriteState('listening')
      }
    }

    async function ensureSession(): Promise<string> {
      const existing = $chatSessionId.get()

      if (existing) {
        return existing
      }

      const res = await requestGateway<{ session_id: string }>('session.create', {})
      setChatSession(res.session_id)

      return res.session_id
    }

    return () => {
      unmounted = true

      if (vadIntervalRef.current) {
        clearInterval(vadIntervalRef.current)
        vadIntervalRef.current = null
      }

      if (timerRef.current) {
        clearInterval(timerRef.current)
      }

      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current)
      }

      if (awaitingReplyTimerRef.current) {
        clearTimeout(awaitingReplyTimerRef.current)
        awaitingReplyTimerRef.current = null
      }

      if (recorderRef.current && recorderRef.current.state !== 'inactive') {
        recorderRef.current.onstop = null

        try {
          recorderRef.current.stop()
        } catch {
          /* ignore */
        }
      }

      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }

      ctx?.close().catch(() => {})
      stopSpeaking()
      setSpriteState('idle')
    }
  }, [requestGateway, gatewayStateRef, onCloseRef])

  // 朗读已完成的回复。本组件不订阅 $chatMessageBodies，仅在 list 与流式状态切换时同步读取最新文本。
  useEffect(() => {
    if (!micActive) {
      return
    }

    const lastItem = list[list.length - 1]

    if (!lastItem || lastItem.role !== 'assistant' || lastAssistantStreaming) {
      return
    }

    const body = $chatMessageBodies.get()[lastItem.id]

    if (!body || body.streaming || body.error || body.cancelled) {
      return
    }

    if (lastItem.id === lastSpokenIdRef.current) {
      return
    }

    lastSpokenIdRef.current = lastItem.id
    awaitingReplyRef.current = false

    if (awaitingReplyTimerRef.current) {
      clearTimeout(awaitingReplyTimerRef.current)
      awaitingReplyTimerRef.current = null
    }

    if (!body.text.trim()) {
      setSpriteState('listening')

      return
    }

    assistantSpeakingRef.current = true
    setSpriteState('speaking')
    const gen = ++speakGenRef.current
    void speak(body.text).then(() => {
      if (gen !== speakGenRef.current) {
        return
      }

      assistantSpeakingRef.current = false
      setSpriteState('listening')
    })
  }, [list, lastAssistantStreaming, micActive])

  const formatTime = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60

    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  const dockPos = computeVoiceCallDockPosition({
    dockH: 288,
    dockW: 320,
    pos,
    scale,
    vh: viewport.height,
    vw: viewport.width
  })

  return (
    // 通话是 ambient 陪伴——精灵窗口保持置顶（§3.7 不变量），
    // 面板跟随精灵移动并锚定在精灵脚下，保持用户视觉焦点统一；拖拽偏移持久化，用户可自行微调。
    <div className="fixed inset-0 z-50 pointer-events-none">
      <div
        className="fixed flex h-72 w-80 flex-col items-center justify-between rounded-3xl border border-white/15 bg-black/75 p-6 text-white shadow-2xl backdrop-blur-xl"
        ref={panelRef}
        style={{
          left: dockPos.left,
          pointerEvents: 'auto',
          top: dockPos.top,
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined,
          willChange: 'left, top, transform'
        }}
      >
        <div
          className="flex w-full cursor-grab items-center justify-between text-xs text-white/60 active:cursor-grabbing"
          {...dragBind}
          title="拖动以移动面板"
        >
          <span className="flex items-center gap-1.5 font-medium text-emerald-400">
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            语音通话中
          </span>
          <div className="flex items-center gap-2">
            <button
              aria-label={subtitlesVisible ? '隐藏字幕' : '显示字幕'}
              aria-pressed={subtitlesVisible}
              className="rounded-full border border-white/15 bg-white/10 px-2 py-0.5 text-[10px] text-white/80 transition hover:bg-white/20 hover:text-white"
              onClick={e => {
                e.stopPropagation()
                setSubtitles(!subtitlesVisible)
              }}
              onPointerDown={e => e.stopPropagation()}
              type="button"
            >
              {subtitlesVisible ? '字幕 开' : '字幕 关'}
            </button>
            <span>{formatTime(durationSec)}</span>
          </div>
        </div>

        <div className="relative flex items-center justify-center my-2">
          {micActive && <div className="absolute h-24 w-24 rounded-full bg-emerald-500/20 animate-ping" />}
          <div className="grid h-20 w-20 place-items-center rounded-full bg-white/10 text-3xl shadow-inner border border-white/20">
            🎙️
          </div>
        </div>

        <WaveformBars active={micActive} values={waveform} />

        {micError ? (
          <p className="text-center text-xs text-amber-300">{micError}</p>
        ) : (
          <div className="flex items-center gap-1 text-xs text-white/70">
            <span className="h-1.5 w-1.5 rounded-full bg-white/60 animate-bounce" />
            <span className="h-1.5 w-1.5 rounded-full bg-white/60 animate-bounce [animation-delay:0.2s]" />
            <span className="h-1.5 w-1.5 rounded-full bg-white/60 animate-bounce [animation-delay:0.4s]" />
            <span className="ml-1">{spriteLabel(spriteState)}</span>
          </div>
        )}

        <button
          className="mt-2 w-full rounded-xl bg-red-500/80 py-2 text-xs font-medium text-white transition hover:bg-red-600 active:scale-95"
          onClick={() => {
            setChatOpen(false)
            onClose()
          }}
          type="button"
        >
          结束通话
        </button>
      </div>
      <SubtitlesOverlay />
    </div>
  )
}

function spriteLabel(state: string): string {
  if (state === 'listening') {
    return '正在倾听…'
  }

  if (state === 'thinking') {
    return '正在思考…'
  }

  if (state === 'speaking') {
    return '正在回答…'
  }

  return '语音通话中…'
}

// 通话中波形：把 analyser 频域分桶成 24 个高度，跟随实时音量跳动。
// 即使没有说话时也保留基础呼吸动画（最小高度 4px），传达"在听"的状态。
function WaveformBars({ values, active }: { values: number[]; active: boolean }): React.JSX.Element {
  return (
    <div
      aria-hidden="true"
      className="flex h-8 w-full items-center justify-center gap-[3px]"
      style={{ pointerEvents: 'none' }}
    >
      {values.map((v, i) => {
        const norm = active ? Math.min(1, v / 200) : 0
        const height = active ? Math.max(4, norm * 32) : 4
        const intensity = active ? Math.min(1, norm + 0.2) : 0.25
        const delay = (i % 6) * 60

        return (
          <span
            className={active ? 'animate-waveform-bounce' : ''}
            key={i}
            style={{
              width: 3,
              height,
              borderRadius: 2,
              background: `rgba(52, 211, 153, ${intensity})`,
              animationDelay: `${delay}ms`
            }}
          />
        )
      })}
    </div>
  )
}
