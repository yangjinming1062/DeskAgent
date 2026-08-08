import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $chatMessages, $chatSessionId, setAssistantError, setChatOpen, setChatSession } from '@/companion/chat-store'
import { $spriteState, setSpriteState } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { speak, stopSpeaking } from '@/companion/tts'
import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'
import { $gatewayState } from '@/shared/store/gateway'

import { SubtitlesOverlay } from './subtitles-overlay'

interface VoiceCallDockProps {
  onClose: () => void
}

const SPEECH_THRESHOLD = 28
const BARGEIN_THRESHOLD = 38
const SILENCE_END_MS = 1300
// Releases the awaiting-reply lock if no message.start ever lands, so the mic re-opens.
const AWAITING_REPLY_TIMEOUT_MS = 60_000

// Live half-duplex voice conversation: the mic stays open; a volume analyser
// segments utterances (speech → sustained silence = turn end). Each finished
// utterance is transcribed (cloud STT), sent as a prompt, and the streamed
// reply is spoken aloud when it completes. Barge-in: speaking aloud while the
// companion talks cuts off the TTS and returns to listening (plan §4.1).
export function VoiceCallDock({ onClose }: VoiceCallDockProps): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
  const messages = useStore($chatMessages)
  const spriteState = useStore($spriteState)
  const [micActive, setMicActive] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const [durationSec, setDurationSec] = useState(0)
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const durationSecRef = useRef(0)
  const audioAnimRef = useRef<number | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const lastActivityTimeRef = useRef<number>(Date.now())
  const analyserRef = useRef<AnalyserNode | null>(null)
  const userSpeakingRef = useRef(false)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const awaitingReplyRef = useRef(false)
  const awaitingReplyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const assistantSpeakingRef = useRef(false)
  const lastSpokenIdRef = useRef<string | null>(null)
  // Reset the last-spoken dedup on a new session so the first reply isn't skipped as a duplicate.
  const chatSessionId = useStore($chatSessionId)
  useEffect(() => {
    lastSpokenIdRef.current = null
  }, [chatSessionId])
  // Stale speak() promises can't drag the sprite to idle after a newer utterance starts.
  const speakGenRef = useRef(0)
  // Mirror gatewayState into a ref so mount effects read it live without re-mounting on reconnects.
  const gatewayStateRef = useRef(gatewayState)
  gatewayStateRef.current = gatewayState
  const { requestGateway } = useGatewayRequest()
  const panelRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useInteractiveRegion('voice-call-dock', panelRef)

  useEffect(() => {
    void window.deskagent.sprite.setAlwaysOnTop({ on: false })

    let ctx: AudioContext | null = null
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then(stream => {
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
                const rec = new MediaRecorder(stream)
                const utteranceChunks: Blob[] = []

                rec.ondataavailable = e => {
                  if (e.data.size > 0) {
                    utteranceChunks.push(e.data)
                  }
                }

                rec.onstop = () => {
                  void transcribeAndSubmit(utteranceChunks)
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

              // Barge-in: user speaks while the companion is talking.
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
                  // Cancel unconditionally: a no-op when already cleared.
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

              audioAnimRef.current = requestAnimationFrame(checkVolume)
            }

            audioAnimRef.current = requestAnimationFrame(checkVolume)
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

    async function transcribeAndSubmit(chunks: Blob[]): Promise<void> {
      if (!chunks.length) {
        return
      }

      const blob = new Blob(chunks, { type: 'audio/webm' })
      let text = ''

      try {
        const reader = new FileReader()

        const dataUrl: string = await new Promise((resolve, reject) => {
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => reject(new Error('read failed'))
          reader.readAsDataURL(blob)
        })

        const res = await window.deskagent.media.stt({ dataUrl, filename: 'voice.webm' })
        text = (res.text ?? '').trim()
      } catch {
        // Surface STT failure to the user instead of silently returning to listening.
        setAssistantError('没听清，请再说一次')
        text = ''
      }

      if (!text || gatewayStateRef.current !== 'open') {
        setSpriteState('listening')

        return
      }

      awaitingReplyRef.current = true
      setSpriteState('thinking')

      // Recover if the WS dies before any message.start lands; cleared on normal completion.
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
      if (audioAnimRef.current) {
        cancelAnimationFrame(audioAnimRef.current)
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
        recorderRef.current.stop()
      }

      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }

      ctx?.close().catch(() => {})
      stopSpeaking()
      setSpriteState('idle')
      void window.deskagent.sprite.setAlwaysOnTop({ on: true })
    }
  }, [requestGateway]) // gatewayState intentionally omitted: a dep would re-mount the mic on reconnect flaps.

  // Speak the assistant's completed reply, then return to listening. The chat
  // event stream (events.ts) owns the streaming + state machine; this effect
  // only reacts to a finalized assistant turn.
  useEffect(() => {
    if (!micActive) {
      return
    }

    const last = messages[messages.length - 1]

    if (!last || last.role !== 'assistant' || last.streaming || last.error) {
      return
    }

    if (last.id === lastSpokenIdRef.current) {
      return
    }

    lastSpokenIdRef.current = last.id
    awaitingReplyRef.current = false

    if (awaitingReplyTimerRef.current) {
      clearTimeout(awaitingReplyTimerRef.current)
      awaitingReplyTimerRef.current = null
    }

    if (!last.text.trim()) {
      setSpriteState('listening')

      return
    }

    assistantSpeakingRef.current = true
    setSpriteState('speaking')
    const gen = ++speakGenRef.current
    void speak(last.text).then(() => {
      if (gen !== speakGenRef.current) {
        return
      }

      assistantSpeakingRef.current = false
      setSpriteState('listening')
    })
  }, [messages, micActive])

  const formatTime = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60

    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-6" style={{ pointerEvents: 'none' }}>
      <div
        className="flex h-72 w-80 flex-col items-center justify-between rounded-3xl border border-white/15 bg-black/75 p-6 text-white shadow-2xl backdrop-blur-xl"
        ref={panelRef}
        style={{ pointerEvents: 'auto' }}
      >
        <div className="flex w-full items-center justify-between text-xs text-white/60">
          <span className="flex items-center gap-1.5 font-medium text-emerald-400">
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            语音通话中
          </span>
          <span>{formatTime(durationSec)}</span>
        </div>

        <div className="relative flex items-center justify-center my-2">
          {micActive && <div className="absolute h-24 w-24 rounded-full bg-emerald-500/20 animate-ping" />}
          <div className="grid h-20 w-20 place-items-center rounded-full bg-white/10 text-3xl shadow-inner border border-white/20">
            🎙️
          </div>
        </div>

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
