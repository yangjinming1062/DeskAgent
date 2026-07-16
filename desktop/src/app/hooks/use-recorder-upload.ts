import { useEffect, useRef, useState } from 'react'

import type { RecorderProgressPayload } from '@/global'

interface RecorderUploadOptions {
  /** Fires alongside the hook's state reset when the upload begins. */
  onUploadStarted?: () => void
  /** Fires alongside the hook's state reset when the upload completes. */
  onFinished?: (fileUrl: string) => void
  /** Fires alongside the hook's state reset when the upload fails. */
  onFailed?: (message: string) => void
}

/**
 * Subscribe to the recorder upload IPC stream and surface the shared
 * progress / error state. The composer and toolbar both need this state
 * (progress bar, percent badge, error chip) and the same four-event
 * subscription; this hook is the single owner of the IPC plumbing.
 *
 * Callers can pass callbacks for events that need component-local side
 * effects (e.g. the composer's `isRecordingUI` highlight toggle and
 * attachment insertion). Callbacks are stored in a ref so the empty-deps
 * subscription always invokes the latest closure.
 */
export const useRecorderUpload = (options: RecorderUploadOptions = {}) => {
  const [progress, setProgress] = useState<RecorderProgressPayload | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const callbacksRef = useRef(options)
  callbacksRef.current = options

  useEffect(() => {
    const unsubStarted = window.zastDesktop?.recorder?.onUploadStarted?.(() => {
      setProgress({ bytesSent: 0, totalBytes: 0, percent: 0, bytesPerSec: 0, etaMs: null })
      setUploadError(null)
      callbacksRef.current.onUploadStarted?.()
    })

    const unsubProgress = window.zastDesktop?.recorder?.onProgress?.(p => {
      setProgress(p)
    })

    const unsubFinished = window.zastDesktop?.recorder?.onFinished?.((fileUrl: string) => {
      setProgress(null)
      setUploadError(null)
      callbacksRef.current.onFinished?.(fileUrl)
    })

    const unsubFailed = window.zastDesktop?.recorder?.onFailed?.(p => {
      setProgress(null)
      setUploadError(p.message)
      callbacksRef.current.onFailed?.(p.message)
    })

    return () => {
      unsubStarted?.()
      unsubProgress?.()
      unsubFinished?.()
      unsubFailed?.()
    }
  }, [])

  return { progress, uploadError }
}
