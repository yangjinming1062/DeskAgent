import { useCallback, useEffect, useRef, useState } from 'react'

import { $chatSessionId, $chatTurnInFlight, setAssistantError, setChatSession } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import { getSpiritAgentConfig } from '@/shared/spiritagent'

type Options = {
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  onTranscribed?: (text: string) => Promise<void> | void
}

// Owns the full voice-message lifecycle: MediaRecorder + chunks + auto-stop
// timer + global-mouseup hook + STT submit. Caller just toggles `recording`
// and listens for `onTranscribed` to push the result to its own state.
//
// The hook is responsible for stopping tracks on stop and on unmount so the
// OS-level mic LED doesn't stay on after the recorder finishes.

export function useVoiceRecorder({ requestGateway, onTranscribed }: Options): {
  recording: boolean
  start: () => void
  stop: () => Promise<void>
} {
  const [recording, setRecording] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const configRef = useRef<{ voice?: { max_recording_seconds?: number } }>({})
  const startPendingRef = useRef<Promise<void> | null>(null)
  const stopRef = useRef<() => Promise<void>>(async () => {})

  useEffect(() => {
    void getSpiritAgentConfig()
      .then(c => {
        configRef.current = { voice: c.voice }
      })
      .catch(() => {
        configRef.current = {}
      })
  }, [])

  const cancelAutoStop = () => {
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
  }

  const stopTracks = (recorder: MediaRecorder | null) => {
    if (!recorder) {
      return
    }

    try {
      recorder.stream.getTracks().forEach(t => t.stop())
    } catch {
      /* already closed */
    }
  }

  const ensureSession = useCallback(async (): Promise<string> => {
    const existing = $chatSessionId.get()

    if (existing) {
      return existing
    }

    const res = await requestGateway<{ session_id: string }>('session.create', {})
    setChatSession(res.session_id)

    return res.session_id
  }, [requestGateway])

  const transcribe = async (blob: Blob): Promise<string | null> => {
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()

        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('read failed'))
        reader.readAsDataURL(blob)
      })

      const res = await window.spiritagent.media.stt({ dataUrl, filename: 'voice.webm' })
      const text = (res.text ?? '').trim()

      return text || null
    } catch {
      setAssistantError('没听清，用打字吧～')

      return null
    }
  }

  const stop = useCallback(async () => {
    if (startPendingRef.current) {
      try {
        await startPendingRef.current
      } catch {
        /* surfaced via start */
      }
    }

    cancelAutoStop()

    const recorder = recorderRef.current

    if (!recorder || recorder.state === 'inactive') {
      setRecording(false)

      return
    }

    const blob = new Blob(chunksRef.current, { type: 'audio/webm' })

    chunksRef.current = []

    recorder.onstop = () => {
      stopTracks(recorder)
      streamRef.current = null
    }

    recorder.stop()
    setRecording(false)

    setSpriteState('thinking')
    const text = await transcribe(blob)

    if (text) {
      try {
        const sessionId = await ensureSession()
        $chatTurnInFlight.set(true)
        await requestGateway('prompt.submit', { session_id: sessionId, text })
        await onTranscribed?.(text)
      } catch (err) {
        $chatTurnInFlight.set(false)
        setSpriteState('idle')
        setAssistantError(err instanceof Error ? err.message : '发送失败')
      }
    }
  }, [requestGateway, onTranscribed, ensureSession])

  stopRef.current = stop

  const start = useCallback(() => {
    let pending: Promise<void> | null = null
    pending = (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const recorder = new MediaRecorder(stream)

        streamRef.current = stream
        recorderRef.current = recorder
        chunksRef.current = []

        recorder.ondataavailable = e => {
          if (e.data.size > 0) {
            chunksRef.current.push(e.data)
          }
        }

        setRecording(true)
        setSpriteState('listening')
        recorder.start()

        const cap = configRef.current.voice?.max_recording_seconds ?? 60

        if (cap > 0) {
          autoStopRef.current = setTimeout(() => {
            if (recorderRef.current?.state === 'recording') {
              void stopRef.current()
            }
          }, cap * 1000)
        }
      } catch {
        setAssistantError('无法使用麦克风录制语音')
        setSpriteState('idle')
      } finally {
        if (startPendingRef.current === pending) {
          startPendingRef.current = null
        }
      }
    })()
    startPendingRef.current = pending
  }, [])

  // `recording` flip drives a global mouseup listener so the user can stop a
  // recording by releasing the button anywhere on screen.
  useEffect(() => {
    if (!recording) {
      return
    }

    const handleGlobalMouseUp = () => {
      void stopRef.current()
    }

    window.addEventListener('mouseup', handleGlobalMouseUp)

    return () => {
      window.removeEventListener('mouseup', handleGlobalMouseUp)
    }
  }, [recording])

  // Unmount cleanup: stop tracks so the OS-level mic LED doesn't stay on.
  useEffect(() => {
    return () => {
      cancelAutoStop()
      const recorder = recorderRef.current

      if (recorder && recorder.state !== 'inactive') {
        stopTracks(recorder)

        try {
          recorder.stop()
        } catch {
          /* already stopped */
        }
      }

      setRecording(false)
    }
  }, [])

  return { recording, start, stop }
}
