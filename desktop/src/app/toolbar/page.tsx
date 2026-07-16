import { useCallback, useEffect, useRef, useState } from 'react'

import { useRecorderUpload } from '@/app/hooks/use-recorder-upload'
// Toolbar runs in its own window without I18nProvider (see main.tsx — the
// toolbar root mounts before the provider stack). Pull the strings directly
// from the zh catalog so they live next to every other copy surface; this
// keeps the future "open the lock" path a one-file edit instead of a hunt.
import { zh } from '@/i18n/zh'
import { Loader2, Mic, MicOff, Pause, Play, Square } from '@/lib/icons'
const t = zh.recordingToolbar

// Above this percent the wire upload is essentially done; the remainder
// is Backend→GCS hand-off (no client progress events). Used by both the
// status label flip and the progress detail block below — keep both in
// lockstep.
const PROCESSING_THRESHOLD = 95

const getSupportedMimeType = () => {
  const types = [
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm;codecs=h264,opus',
    'video/webm',
    'video/mp4;codecs=avc1,opus',
    'video/mp4;codecs=avc1',
    'video/mp4'
  ]

  for (const type of types) {
    if (MediaRecorder.isTypeSupported(type)) {
      return type
    }
  }

  return ''
}

// Cancellable sleep — returns a promise + its abort handle so an unmount
// during the 800ms pre-display delay doesn't leave getDisplayMedia firing
// on a destroyed renderer.
function makeCancellableDelay(ms: number) {
  let timer: ReturnType<typeof setTimeout> | null = null

  const promise = new Promise<void>(resolve => {
    timer = setTimeout(resolve, ms)
  })

  return {
    promise,
    cancel: () => {
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    }
  }
}

// rAF-throttled IPC. mousemove fires per-pixel; spamming moveToolbar across
// the renderer↔main boundary saturates the IPC channel and visibly lags the
// dragged toolbar.
function rafThrottle<F extends (...args: any[]) => void>(fn: F): F {
  let pending: number | null = null
  let lastArgs: any[] | null = null

  const wrapped = ((...args: any[]) => {
    lastArgs = args

    if (pending !== null) {
      return
    }

    pending = requestAnimationFrame(() => {
      pending = null

      if (lastArgs) {
        fn(...lastArgs)
        lastArgs = null
      }
    })
  }) as F

  return wrapped
}

export const RecordingToolbar = () => {
  const [isRecording, setIsRecording] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [isStopping, setIsStopping] = useState(false)
  const [duration, setDuration] = useState(0)
  const [isMuted, setIsMuted] = useState(false)
  const [hasMic, setHasMic] = useState(false)
  const { progress, uploadError } = useRecorderUpload()

  const isMutedRef = useRef(isMuted)
  useEffect(() => {
    isMutedRef.current = isMuted
  }, [isMuted])

  const streamRef = useRef<MediaStream | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const cleanupAudioRef = useRef<(() => void) | null>(null)
  const micTrackRef = useRef<MediaStreamTrack | null>(null)
  // Chain of in-flight setData promises. We `await` each one before resolving
  // the next dataavailable handler so chunks land in recordingBuffers in the
  // order MediaRecorder emitted them — without sequencing, arrayBuffer() can
  // resolve out of order and produce a corrupted webm.
  const chunkSendChainRef = useRef<Promise<void>>(Promise.resolve())

  const [isDragging, setIsDragging] = useState(false)
  const dragStartRef = useRef<{ mouseX: number; mouseY: number; winX: number; winY: number } | null>(null)

  const MAX_DURATION = 30 * 60 * 1000

  useEffect(() => {
    navigator.mediaDevices.enumerateDevices().then(devices => {
      setHasMic(devices.some(d => d.kind === 'audioinput'))
    })
  }, [])

  useEffect(() => {
    if (!isRecording || isPaused) {
      return
    }

    const interval = setInterval(() => {
      setDuration(d => d + 1000)
    }, 1000)

    return () => clearInterval(interval)
  }, [isRecording, isPaused])

  useEffect(() => {
    const startDelay = makeCancellableDelay(800)

    const startRecordingFlow = async () => {
      await startDelay.promise

      const api = window.zastDesktop?.recorder

      if (!api) {
        return
      } // preload not yet exposed; abort cleanly

      try {
        chunksRef.current = []

        const displayStream = await navigator.mediaDevices.getDisplayMedia({
          video: { displaySurface: 'monitor', frameRate: 30 },
          audio: true
        })

        let finalStream = displayStream

        let micStream: MediaStream | null = null

        try {
          micStream = await navigator.mediaDevices.getUserMedia({ audio: true })

          if (micStream && micStream.getAudioTracks().length > 0) {
            micTrackRef.current = micStream.getAudioTracks()[0]
            micTrackRef.current.enabled = !isMutedRef.current
          }
        } catch (e) {
          console.warn('[RecordingToolbar] Microphone not available or denied:', e)
        }

        if (micStream && micStream.getAudioTracks().length > 0) {
          try {
            const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)()
            const destination = audioContext.createMediaStreamDestination()
            const sources: MediaStreamAudioSourceNode[] = []

            if (displayStream.getAudioTracks().length > 0) {
              const displayAudioSource = audioContext.createMediaStreamSource(
                new MediaStream([displayStream.getAudioTracks()[0]])
              )

              displayAudioSource.connect(destination)
              sources.push(displayAudioSource)
            }

            const micAudioSource = audioContext.createMediaStreamSource(
              new MediaStream([micStream.getAudioTracks()[0]])
            )

            micAudioSource.connect(destination)
            sources.push(micAudioSource)

            finalStream = new MediaStream([displayStream.getVideoTracks()[0], ...destination.stream.getAudioTracks()])

            cleanupAudioRef.current = () => {
              sources.forEach(s => s.disconnect())
              audioContext.close()
              micStream?.getTracks().forEach(track => track.stop())
            }
          } catch (err) {
            console.error('[RecordingToolbar] Failed to mix audio, falling back to display stream only:', err)
            micStream.getTracks().forEach(track => track.stop())
          }
        }

        streamRef.current = displayStream

        const mimeType = getSupportedMimeType()

        const recorder = new MediaRecorder(finalStream, {
          mimeType,
          videoBitsPerSecond: 500000
        })

        recorder.ondataavailable = e => {
          if (!e.data || e.data.size === 0) {
            return
          }

          chunksRef.current.push(e.data)
          // Sequence: chain the IPC send onto the previous promise so chunks
          // arrive in recorder.cjs in emission order.
          chunkSendChainRef.current = chunkSendChainRef.current
            .then(async () => {
              const buffer = await e.data.arrayBuffer()
              await api.setData?.(buffer)
            })
            .catch(err => console.error('[RecordingToolbar] setData failed:', err))
        }

        // If the user revokes screen capture from the OS UI, treat as stop.
        displayStream.getVideoTracks()[0]?.addEventListener('ended', () => {
          if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
            mediaRecorderRef.current.stop()
          }
        })

        mediaRecorderRef.current = recorder
        recorder.start(1000)
        setIsRecording(true)
      } catch (error) {
        console.error('[RecordingToolbar] Failed to initialize display media capture:', error)
      }
    }

    startRecordingFlow()

    return () => {
      startDelay.cancel()

      if (cleanupAudioRef.current) {
        cleanupAudioRef.current()
      }

      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }

      if (micTrackRef.current) {
        micTrackRef.current.stop()
      }
    }
  }, [])

  const formatDuration = (ms: number) => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)

    return `${minutes.toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`
  }

  const handlePause = async () => {
    await window.zastDesktop?.recorder?.pause?.()
    setIsPaused(true)

    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.pause()
    }
  }

  const handleResume = async () => {
    await window.zastDesktop?.recorder?.resume?.()
    setIsPaused(false)

    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'paused') {
      mediaRecorderRef.current.resume()
    }
  }

  const handleStop = useCallback(async () => {
    if (isStopping) {
      return
    }

    setIsStopping(true)
    const api = window.zastDesktop?.recorder

    try {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        await new Promise<void>(resolve => {
          if (!mediaRecorderRef.current) {
            return resolve()
          }

          mediaRecorderRef.current.onstop = () => resolve()
          mediaRecorderRef.current.stop()
        })
      }

      // Recording has actually stopped here — flip UI out of "录制中" state
      // immediately so the user-visible timer freezes and the pulsing dot
      // stops. The "保存中..." label still accurately reflects the upload
      // that follows; the toolbar window will close when finishUpload
      // resolves via api.stop() below.
      setIsRecording(false)

      // Drain pending chunk sends so finishUpload sees every chunk.
      await chunkSendChainRef.current.catch(() => {})

      try {
        if (cleanupAudioRef.current) {
          cleanupAudioRef.current()
          cleanupAudioRef.current = null
        }

        if (streamRef.current) {
          streamRef.current.getTracks().forEach(track => track.stop())
        }

        if (micTrackRef.current) {
          micTrackRef.current.stop()
        }
      } catch (cleanupErr) {
        console.error('[RecordingToolbar] Media cleanup error:', cleanupErr)
      }

      // Keep the toolbar visible during the GCS upload so the user sees
      // "上传中..." feedback with a spinner instead of the toolbar
      // vanishing the moment they click stop. finishUpload broadcasts
      // recorder:finished when done; we close the toolbar window then.
      await api?.finishUpload?.()
      await api?.stop?.()
    } catch (error) {
      console.error('[RecordingToolbar] Failed to stop recording cleanly:', error)
      setIsRecording(false)
      await api?.stop?.()
    } finally {
      setIsStopping(false)
    }
  }, [isStopping])

  const handleToggleMute = async () => {
    const nextMute = !isMuted
    setIsMuted(nextMute)

    if (micTrackRef.current) {
      micTrackRef.current.enabled = !nextMute
    }
  }

  useEffect(() => {
    if (isRecording && !isPaused) {
      const remaining = MAX_DURATION - duration

      if (remaining <= -60000) {
        handleStop()
      }
    }
  }, [duration, isRecording, isPaused, handleStop, MAX_DURATION])

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) {
      return
    }

    let target = e.target as HTMLElement | null

    while (target && target !== e.currentTarget) {
      if (target.tagName === 'BUTTON' || target.getAttribute('role') === 'button') {
        return
      }

      target = target.parentElement
    }

    setIsDragging(true)
    dragStartRef.current = {
      mouseX: e.screenX,
      mouseY: e.screenY,
      winX: window.screenX,
      winY: window.screenY
    }
  }

  useEffect(() => {
    if (!isDragging) {
      return
    }

    const moveThrottled = rafThrottle((newX: number, newY: number) => {
      window.zastDesktop?.recorder?.moveToolbar?.({ x: newX, y: newY }).catch(console.error)
    })

    const handleMouseMove = (e: MouseEvent) => {
      if (!dragStartRef.current) {
        return
      }

      const deltaX = e.screenX - dragStartRef.current.mouseX
      const deltaY = e.screenY - dragStartRef.current.mouseY
      moveThrottled(dragStartRef.current.winX + deltaX, dragStartRef.current.winY + deltaY)
    }

    const handleMouseUp = () => {
      setIsDragging(false)
      dragStartRef.current = null
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging])

  const remaining = MAX_DURATION - duration
  const isWarning = remaining <= 0
  const warningText = isWarning ? t.timeoutNotice(Math.max(0, Math.ceil((remaining + 60000) / 1000))) : ''

  // Past 95% the wire upload is essentially done — the remaining time is
  // Backend→GCS hand-off, which has no progress events. Showing the
  // actual percent and "0s left" makes the user think the bar is stuck.
  const showWireProgress = progress !== null && progress.percent < PROCESSING_THRESHOLD
  const isFinalizing = progress !== null && progress.percent >= PROCESSING_THRESHOLD

  // Status indicator: four mutually-exclusive states (error / finalizing /
  // uploading / paused-or-recording / idle) — pick a label, then a color.
  const statusLabel = uploadError
    ? t.statusUploadFailed
    : isFinalizing
      ? t.statusProcessing
      : showWireProgress
        ? `${progress!.percent}%`
        : isRecording
          ? isPaused
            ? t.statusPaused
            : t.statusRecording
          : t.statusReady

  const status = uploadError
    ? { color: '#ff5252', label: statusLabel, animate: false }
    : isFinalizing || showWireProgress
      ? { color: '#2196F3', label: statusLabel, animate: false }
      : isRecording
        ? { color: isPaused ? '#ccc' : '#ff4444', label: statusLabel, animate: !isPaused }
        : { color: '#ccc', label: statusLabel, animate: false }

  return (
    <div
      className="recording-toolbar-root"
      onMouseDown={handleMouseDown}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
        padding: '8px 16px',
        backgroundColor: 'rgba(26, 26, 26, 0.95)',
        borderRadius: '12px',
        color: '#fff',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '14px',
        boxShadow: '0 4px 24px rgba(0, 0, 0, 0.3)',
        width: '100%',
        height: '100vh',
        boxSizing: 'border-box',
        cursor: isDragging ? 'grabbing' : 'grab',
        userSelect: 'none',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
        position: 'relative'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <div
          style={{
            width: '12px',
            height: '12px',
            borderRadius: '50%',
            backgroundColor: status.color,
            animation: status.animate ? 'zast-toolbar-pulse 1.5s infinite' : 'none'
          }}
        />
        <span style={{ fontWeight: 500 }}>{status.label}</span>
      </div>

      {progress !== null && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            fontVariantNumeric: 'tabular-nums',
            fontSize: '12px',
            opacity: 0.85
          }}
        >
          <span>
            {(progress.bytesSent / 1024 / 1024).toFixed(1)} MB / {(progress.totalBytes / 1024 / 1024).toFixed(1)} MB
          </span>
          {showWireProgress && (
            <>
              <span>·</span>
              <span>{(progress.bytesPerSec / 1024 / 1024).toFixed(2)} MB/s</span>
              {progress.etaMs != null && (
                <>
                  <span>·</span>
                  <span>{t.uploadingEta(Math.ceil(progress.etaMs / 1000))}</span>
                </>
              )}
            </>
          )}
        </div>
      )}

      {uploadError && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            color: '#ff5252',
            fontSize: '12px',
            maxWidth: '300px',
            overflow: 'hidden',
            textOverflow: 'ellipsis'
          }}
        >
          {uploadError}
        </div>
      )}

      {isRecording && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontVariantNumeric: 'tabular-nums', color: isWarning ? '#ff4444' : '#fff' }}>
            {formatDuration(duration)}
          </span>
          {isWarning && (
            <span
              style={{
                fontSize: '12px',
                color: '#ff4444',
                fontWeight: 'bold',
                animation: 'zast-toolbar-pulse 1s infinite'
              }}
            >
              {warningText}
            </span>
          )}
        </div>
      )}

      {hasMic && (
        <button
          onClick={handleToggleMute}
          style={{
            padding: '4px 8px',
            backgroundColor: isMuted ? '#666' : '#ff4444',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '4px'
          }}
        >
          {isMuted ? <MicOff size={14} /> : <Mic size={14} />}
        </button>
      )}

      <div style={{ display: 'flex', gap: '8px', marginLeft: 'auto' }}>
        {isPaused ? (
          <button
            onClick={handleResume}
            style={{
              padding: '6px 12px',
              backgroundColor: '#2196F3',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
          >
            <Play size={14} /> {t.resume}
          </button>
        ) : (
          <button
            disabled={!isRecording}
            onClick={handlePause}
            style={{
              padding: '6px 12px',
              backgroundColor: '#FF9800',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: isRecording ? 'pointer' : 'not-allowed',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              opacity: isRecording ? 1 : 0.6
            }}
          >
            <Pause size={14} /> {t.pause}
          </button>
        )}

        <button
          disabled={!isRecording || isStopping}
          onClick={handleStop}
          style={{
            padding: '6px 12px',
            backgroundColor: '#f44336',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: isRecording ? 'pointer' : 'not-allowed',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            opacity: isRecording && !isStopping ? 1 : 0.6
          }}
        >
          <Square size={14} /> {isStopping ? t.uploading : t.stop}
          {isStopping && <Loader2 className="size-3 animate-spin" />}
        </button>
      </div>

      {/* Bottom progress bar: shows during upload (or red on failure). Anchored
          to the toolbar's bottom edge so it overlays the rounded container. */}
      {(progress || uploadError) && (
        <div
          aria-label="upload-progress"
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            bottom: 0,
            height: '4px',
            backgroundColor: 'rgba(255, 255, 255, 0.1)',
            overflow: 'hidden'
          }}
        >
          <div
            style={{
              width: uploadError ? '100%' : `${progress?.percent ?? 0}%`,
              height: '100%',
              backgroundColor: uploadError ? '#ff5252' : '#2196F3',
              transition: 'width 200ms ease-out, background-color 200ms ease-out'
            }}
          />
        </div>
      )}
    </div>
  )
}
