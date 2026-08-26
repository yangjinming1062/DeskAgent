import { useStore } from '@nanostores/react'
import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { reportInteractionStat } from '@/companion/activity'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatOpen,
  $chatSessionId,
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  pushAffectTraceMessage,
  pushUserMessage,
  setAssistantError,
  setChatOpen,
  setChatSession,
  showMediaHint
} from '@/companion/chat-store'
import { $spriteState, setSpriteState } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { $companionVoiceId, $subtitles, setSubtitles } from '@/companion/prefs'
import {
  $spatialPos,
  $spatialScale,
  computeVoiceCallDockPosition,
  endDragAt,
  ensureVoiceDockRoom,
  releaseVoiceDockRoom,
  startDrag,
  updateDragPosition,
  VOICE_DOCK_H,
  VOICE_DOCK_W
} from '@/companion/spatial'
import { stopSpeaking } from '@/companion/tts'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

import { createPcmCapture, type PcmCapture } from './pcm-capture'
import { VoiceSegmentPlayer } from './segment-player'
import { SubtitlesOverlay } from './subtitles-overlay'
import { VoiceSessionClient, type VoiceSessionStatus, type VoiceTurnEndPayload } from './voice-session'

interface VoiceCallDockProps {
  onClose: () => void
}

const SPEECH_THRESHOLD = 28
const BARGEIN_THRESHOLD = 38
const SILENCE_END_MS = 1300
const WAVE_BARS = 24

export const VOICE_CALL_AUDIO_CONSTRAINTS: MediaTrackConstraints = {
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  sampleRate: 16000
}

// 实时半双工语音通话：麦克风持续收音，本地 VAD 判定说话起止与插话打断；
// 回复由服务端实时语音会话编排（ASR → LLM 按句流式 → TTS 分段推送，PROTOCOL §1.7），
// 客户端只负责采集上行 PCM、顺序播放下行分段与字幕/状态呈现。
// 面板与精灵刚体一体：恒锚在精灵脚下，拖动面板即拖动精灵（位移直写精灵位置）。
export function VoiceCallDock({ onClose }: VoiceCallDockProps): React.JSX.Element {
  const { requestGateway } = useGatewayRequest()
  const chatSessionId = useStore($chatSessionId)
  const spriteState = useStore($spriteState)
  const subtitlesVisible = useStore($subtitles)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const [micActive, setMicActive] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const [connStatus, setConnStatus] = useState<VoiceSessionStatus>('connecting')
  const [panelError, setPanelError] = useState<string | null>(null)
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
  const lastActivityTimeRef = useRef<number>(Date.now())
  const analyserRef = useRef<AnalyserNode | null>(null)
  const userSpeakingRef = useRef(false)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 回合进行中（转写/思考/分段下发）或下行音频还在播——期间禁止开新 utterance，
  // 用户开口超更高阈值走打断。
  const turnActiveRef = useRef(false)
  const sessionRef = useRef<VoiceSessionClient | null>(null)
  const playerRef = useRef<VoiceSegmentPlayer | null>(null)
  const captureRef = useRef<PcmCapture | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useLatestRef(onClose)

  useInteractiveRegion('voice-call-dock', panelRef)

  // 面板与精灵刚体一体（DESIGN §6.1）：开启时脚下空间不足则上提精灵让位，
  // 挂断后回落原位；用户在通话中拖动即接管位置，回落取消。
  useEffect(() => {
    ensureVoiceDockRoom()

    return () => releaseVoiceDockRoom()
  }, [])

  // 拖动面板 = 拖动精灵：位移直写精灵位置（面板位置由精灵位置派生），
  // 释放走与精灵本体一致的落点结算（抛掷自由落体落在面板上、落地钳制）。
  const dockDragRef = useRef<{
    startX: number
    startY: number
    origin: { x: number; y: number }
    lastX: number
    lastY: number
    lastTime: number
  } | null>(null)

  const dockVelRef = useRef<{ vx: number; vy: number }>({ vx: 0, vy: 0 })

  const onDockPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    // 仅响应左键拖拽；忽略中键/右键点击以及带修饰键的拖拽。
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) {
      return
    }

    // 点中拖拽柄里的控件（字幕开关等）时不开启拖拽。
    if ((e.target as HTMLElement).closest('button, input, textarea, select, a, [role="button"]')) {
      return
    }

    e.currentTarget.setPointerCapture(e.pointerId)
    dockDragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origin: $spatialPos.get(),
      lastX: e.clientX,
      lastY: e.clientY,
      lastTime: performance.now()
    }
    dockVelRef.current = { vx: 0, vy: 0 }
    startDrag()
  }

  const onDockPointerMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = dockDragRef.current

    if (!d) {
      return
    }

    const now = performance.now()
    const dt = Math.max(1, now - d.lastTime)
    dockVelRef.current = { vx: (e.clientX - d.lastX) / dt, vy: (e.clientY - d.lastY) / dt }
    d.lastX = e.clientX
    d.lastY = e.clientY
    d.lastTime = now

    updateDragPosition(
      { x: d.origin.x + (e.clientX - d.startX), y: d.origin.y + (e.clientY - d.startY) },
      dockVelRef.current
    )
  }

  const onDockPointerUp = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = dockDragRef.current

    if (!d) {
      return
    }

    dockDragRef.current = null
    e.currentTarget.releasePointerCapture(e.pointerId)
    endDragAt($spatialPos.get(), dockVelRef.current)
  }

  const isBusy = (): boolean => turnActiveRef.current || Boolean(playerRef.current?.playing)

  useEffect(() => {
    let unmounted = false
    let ctx: AudioContext | null = null
    let session: VoiceSessionClient | null = null
    let capture: PcmCapture | null = null
    const player = new VoiceSegmentPlayer()
    playerRef.current = player

    player.onAllPlayed = () => {
      if (!turnActiveRef.current) {
        setSpriteState('listening')
      }
    }

    const noteActivity = (): void => {
      lastActivityTimeRef.current = Date.now()
    }

    const startUtterance = (): void => {
      userSpeakingRef.current = true
      setSpriteState('listening')
      captureRef.current?.start()
      sessionRef.current?.sendUtteranceStart()
      clearTimeout(silenceTimerRef.current ?? undefined)
      silenceTimerRef.current = null
    }

    const finishUtterance = (): void => {
      captureRef.current?.stop()
      sessionRef.current?.sendUtteranceEnd(false)
      userSpeakingRef.current = false
    }

    navigator.mediaDevices
      ?.getUserMedia({ audio: VOICE_CALL_AUDIO_CONSTRAINTS })
      .then(async stream => {
        if (unmounted) {
          stream.getTracks().forEach(track => track.stop())

          return
        }

        streamRef.current = stream
        setMicActive(true)
        setSpriteState('listening')

        capture = await createPcmCapture(stream, chunk => sessionRef.current?.sendPcmChunk(chunk))
        captureRef.current = capture

        const AudioContextClass = getAudioContextCtor()

        if (!AudioContextClass) {
          return
        }

        ctx = new AudioContextClass()
        const source = ctx.createMediaStreamSource(stream)
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 256
        source.connect(analyser)
        analyserRef.current = analyser
        const dataArray = new Uint8Array(analyser.frequencyBinCount)

        const checkVolume = () => {
          analyser.getByteFrequencyData(dataArray)
          const avg = dataArray.reduce((acc, val) => acc + val, 0) / dataArray.length

          if (avg > SPEECH_THRESHOLD || isBusy()) {
            noteActivity()
          }

          // 打断：用户趁伙伴说话时开口——本地即刻停播并通知服务端取消回合。
          if (avg > BARGEIN_THRESHOLD && isBusy()) {
            player.stopAll()
            sessionRef.current?.sendInterrupt()
            turnActiveRef.current = false
            setSpriteState('listening')
          }

          if (!isBusy()) {
            if (!userSpeakingRef.current && avg > SPEECH_THRESHOLD) {
              startUtterance()
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

        // VAD 就绪后再建语音会话：绑定当前聊天会话（无则经网关新建），
        // 建立失败（网关不可用 / 未配置云端语音供应商）进面板错误条，不阻断本地收音显示。
        try {
          const existing = $chatSessionId.get()
          const sessionId = existing ?? (await requestGateway<{ session_id: string }>('session.create', {})).session_id

          if (!existing) {
            setChatSession(sessionId)
          }

          session = await VoiceSessionClient.open(sessionId, $companionVoiceId.get(), {
            onStatus: (status, message) => {
              if (unmounted) {
                return
              }

              setConnStatus(status)

              if (status === 'closed' && message) {
                setPanelError(message)
              }
            },
            onAsrFinal: text => {
              if (unmounted) {
                return
              }

              setPanelError(null)
              noteActivity()

              if (text) {
                pushUserMessage(text)
              }

              turnActiveRef.current = true
              setSpriteState('thinking')
            },
            onLlmStart: () => {
              if (unmounted) {
                return
              }

              beginAssistantMessage()
              setSpriteState('thinking')
            },
            onTtsSegment: (_segIndex, text, segment) => {
              if (unmounted) {
                return
              }

              noteActivity()

              if (text) {
                appendAssistantDelta(text)
              }

              setSpriteState('speaking')
              player.enqueue(segment)
            },
            onTurnEnd: payload => {
              if (unmounted) {
                return
              }

              noteActivity()
              turnActiveRef.current = false
              finalizeAssistantMessage(payload.text, payload.media?.length ? payload.media : undefined)
              applyTurnEndExtras(payload)

              if (!player.playing) {
                setSpriteState('listening')
              }
            },
            onTurnError: (stage, _code, message) => {
              if (unmounted) {
                return
              }

              noteActivity()
              setPanelError(stage === 'asr' ? message : `${stageLabel(stage)}：${message}`)

              // LLM 失败时聊天侧的流式气泡需要收尾成错误态；TTS 段失败只上面板（文字不丢）。
              if (stage === 'llm') {
                setAssistantError(message)
              }
            },
            onInterrupted: () => {
              if (unmounted) {
                return
              }

              noteActivity()
              turnActiveRef.current = false
              setSpriteState('listening')
            }
          })
          sessionRef.current = session
        } catch (err: unknown) {
          if (!unmounted) {
            setPanelError(err instanceof Error ? err.message : '语音会话建立失败')
          }
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

      sessionRef.current?.close()
      sessionRef.current = null
      captureRef.current?.close()
      captureRef.current = null
      playerRef.current?.close()
      playerRef.current = null

      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }

      ctx?.close().catch(() => {})
      stopSpeaking()
      setSpriteState('idle')
    }
  }, [requestGateway, chatSessionId, onCloseRef])

  const formatTime = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60

    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  const dockPos = computeVoiceCallDockPosition(pos, scale)

  return (
    // 通话是 ambient 陪伴——精灵窗口保持置顶（§3.7 不变量），
    // 面板与精灵刚体一体：锚定在精灵脚下并跟随移动，拖动面板即拖动精灵，视觉焦点不分散。
    <div className="fixed inset-0 z-50 pointer-events-none">
      <div
        className="fixed flex flex-col items-center justify-between rounded-3xl border border-white/15 bg-black/75 p-6 text-white shadow-2xl backdrop-blur-xl"
        ref={panelRef}
        style={{
          height: VOICE_DOCK_H,
          left: dockPos.left,
          pointerEvents: 'auto',
          top: dockPos.top,
          width: VOICE_DOCK_W,
          willChange: 'left, top'
        }}
      >
        <div
          className="flex w-full cursor-grab items-center justify-between text-xs text-white/60 active:cursor-grabbing"
          onPointerCancel={onDockPointerUp}
          onPointerDown={onDockPointerDown}
          onPointerMove={onDockPointerMove}
          onPointerUp={onDockPointerUp}
          title="拖动面板（精灵跟随移动）"
        >
          <span className="flex items-center gap-1.5 font-medium text-emerald-400">
            <span
              className={`h-2 w-2 rounded-full ${connStatus === 'ready' ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400 animate-pulse'}`}
            />
            {connStatus === 'ready' ? '语音通话中' : connStatus === 'reconnecting' ? '重连中…' : '连接中…'}
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
          {micActive && connStatus === 'ready' && (
            <div className="absolute h-24 w-24 rounded-full bg-emerald-500/20 animate-ping" />
          )}
          <div className="grid h-20 w-20 place-items-center rounded-full bg-white/10 text-3xl shadow-inner border border-white/20">
            🎙️
          </div>
        </div>

        <WaveformBars active={micActive} values={waveform} />

        {/* 字幕内嵌面板（DESIGN §6.1）：占满中部弹性区，流式内容自动滚到最新一句 */}
        <div className="flex min-h-0 w-full flex-1">
          <SubtitlesOverlay />
        </div>

        {micError ? (
          <p className="text-center text-xs text-amber-300">{micError}</p>
        ) : panelError ? (
          <p className="text-center text-xs text-amber-300" title={panelError}>
            {panelError}
          </p>
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
    </div>
  )
}

// turn.end 的非正文外溢效果与 events.ts 的 message.complete 处理保持同构：
// 纯情绪回合补情绪痕迹行、媒体送达轻提示、互动统计。
function applyTurnEndExtras(payload: VoiceTurnEndPayload): void {
  const text = payload.text ?? ''
  const emotion = payload.affect?.emotion
  const actions = payload.affect?.actions ?? []

  if (!text.trim() && ((emotion && emotion !== 'neutral') || actions.length > 0)) {
    pushAffectTraceMessage()
  }

  if (payload.media?.length && !$chatOpen.get()) {
    showMediaHint(
      payload.media.some(m => m.type === 'video')
        ? '🎬 我生成了一段视频，点这里查看'
        : '🖼️ 我生成了一张图片，点这里查看'
    )
  }

  if (text.trim()) {
    reportInteractionStat('chat_turn')
  }
}

function stageLabel(stage: string): string {
  if (stage === 'asr') {
    return '语音识别'
  }

  if (stage === 'tts') {
    return '语音合成'
  }

  if (stage === 'llm') {
    return '回复生成'
  }

  return '语音会话'
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
