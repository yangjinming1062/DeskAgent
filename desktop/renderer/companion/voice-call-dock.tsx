import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { setSpriteState } from '@/companion/companion-store'
import { $gatewayState } from '@/shared/store/gateway'

interface VoiceCallDockProps {
  onClose: () => void
}

export function VoiceCallDock({ onClose }: VoiceCallDockProps) {
  const gatewayState = useStore($gatewayState)
  const [micActive, setMicActive] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const [durationSec, setDurationSec] = useState(0)
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    // Focus window and enable mouse interaction
    void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: false })
    void window.deskagent.sprite.setAlwaysOnTop({ on: false })

    // Request microphone
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then(stream => {
        streamRef.current = stream
        setMicActive(true)
        setSpriteState('listening')
      })
      .catch(() => {
        setMicError('无法接入麦克风，请检查系统权限')
        setSpriteState('idle')
      })

    // Auto duration timer & 3-min silent timeout
    timerRef.current = setInterval(() => {
      setDurationSec(prev => {
        if (prev >= 180) {
          onClose()
          return prev
        }
        return prev + 1
      })
    }, 1000)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }
      setSpriteState('idle')
      void window.deskagent.sprite.setAlwaysOnTop({ on: true })
      void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
    }
  }, [onClose])

  const formatTime = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-6" style={{ pointerEvents: 'auto' }}>
      <div className="flex h-72 w-80 flex-col items-center justify-between rounded-3xl border border-white/15 bg-black/75 p-6 text-white shadow-2xl backdrop-blur-xl">
        <div className="flex w-full items-center justify-between text-xs text-white/60">
          <span className="flex items-center gap-1.5 font-medium text-emerald-400">
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            语音通话中
          </span>
          <span>{formatTime(durationSec)}</span>
        </div>

        <div className="relative flex items-center justify-center my-2">
          {micActive && (
            <div className="absolute h-24 w-24 rounded-full bg-emerald-500/20 animate-ping" />
          )}
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
            <span className="ml-1">正在倾听…</span>
          </div>
        )}

        <button
          className="mt-2 w-full rounded-xl bg-red-500/80 py-2 text-xs font-medium text-white transition hover:bg-red-600 active:scale-95"
          onClick={onClose}
          type="button"
        >
          结束通话
        </button>
      </div>
    </div>
  )
}
