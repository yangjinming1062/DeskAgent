import { useCallback, useEffect, useRef, useState } from 'react'

import { $chatSessionId, $chatTurnInFlight, setAssistantError, setChatSession } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import {
  getAudioExtensionForMime,
  getSupportedOpusMimeType,
  isMediaBusyError,
  VOICE_CALL_AUDIO_CONSTRAINTS
} from '@/companion/voice-call-dock'
import { getSpiritAgentConfig } from '@/shared/spiritagent'

type Options = {
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  onTranscribed?: (text: string) => Promise<void> | void
}

// 语音消息生命周期管理：录音、自动停止、全局事件解绑、音轨清理与转写提交。

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
      /* 已关闭 */
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
    if (!blob || blob.size === 0) {
      return null
    }

    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()

        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('read failed'))
        reader.readAsDataURL(blob)
      })

      const ext = getAudioExtensionForMime(blob.type)
      const res = await window.spiritagent.media.stt({ dataUrl, filename: `voice.${ext}` })
      const text = (res.text ?? '').trim()

      return text || null
    } catch (err: unknown) {
      setAssistantError(isMediaBusyError(err) ? '语音服务正忙，请稍候再试' : '没听清，用打字吧～')

      return null
    }
  }

  const stop = useCallback(async () => {
    if (startPendingRef.current) {
      try {
        await startPendingRef.current
      } catch {
        /* 由 start 抛出 */
      }
    }

    cancelAutoStop()

    const recorder = recorderRef.current

    if (!recorder || recorder.state === 'inactive') {
      setRecording(false)

      return
    }

    const mimeType = recorder.mimeType || getSupportedOpusMimeType() || 'audio/webm'

    const blob = await new Promise<Blob | null>(resolve => {
      recorder.onstop = () => {
        stopTracks(recorder)
        streamRef.current = null
        const chunks = chunksRef.current
        chunksRef.current = []

        if (chunks.length === 0) {
          resolve(null)

          return
        }

        resolve(new Blob(chunks, { type: mimeType }))
      }

      try {
        recorder.stop()
      } catch {
        resolve(null)
      }
    })

    setRecording(false)

    if (!blob || blob.size === 0) {
      setSpriteState('idle')

      return
    }

    setSpriteState('thinking')
    const text = await transcribe(blob)

    if (text) {
      try {
        const sessionId = await ensureSession()
        $chatTurnInFlight.set(true)
        await onTranscribed?.(text)
        await requestGateway('prompt.submit', { session_id: sessionId, text })
      } catch (err) {
        $chatTurnInFlight.set(false)
        setSpriteState('idle')
        setAssistantError(err instanceof Error ? err.message : '发送失败')
      }
    } else {
      setSpriteState('idle')
    }
  }, [requestGateway, onTranscribed, ensureSession])

  stopRef.current = stop

  const start = useCallback(() => {
    let pending: Promise<void> | null = null
    pending = (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: VOICE_CALL_AUDIO_CONSTRAINTS })
        const mimeType = getSupportedOpusMimeType()
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)

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

  // `recording` 切换驱动一个全局 mouseup 监听器，
  // 用户可在屏幕任意位置松开按钮即可停止录音。
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

  // 卸载清理：关闭音轨，避免 OS 级别麦克风指示灯保持亮起。
  useEffect(() => {
    return () => {
      cancelAutoStop()
      const recorder = recorderRef.current

      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null
        stopTracks(recorder)

        try {
          recorder.stop()
        } catch {
          /* 已停止 */
        }
      }

      setRecording(false)
    }
  }, [])

  return { recording, start, stop }
}
