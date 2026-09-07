import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'
import { useState } from 'react'

import {
  $responseMode,
  $screenLocked,
  $spriteState,
  playDataUrl,
  playSpriteActionSequence,
  requestSynth,
  setSpriteState,
  type SpriteEmotion,
  stopAudio
} from '@/companion'
import { ChevronDown, FileText, Loader2, Volume2 } from '@/shared/lib/icons'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { $surfaceOpen, isLivingProxyWindow } from '@/shared/store/surfaces'

import { $chatMessageBodies, type ChatMessageBody } from './chat-store'

export const TTS_MAX_TEXT_CHARS = 4000

const audioDataUrlCache = new Map<string, string>()
const autoPlayQueue: string[] = []

const $voiceBarPlayingId = atom<string | null>(null)
const $voiceBarLoadingId = atom<string | null>(null)

let synthEpoch = 0
let activePlayToken = 0
let turnPendingEmotion: { actions: string[]; emotion: SpriteEmotion } | null = null

export function isLivingVoiceBarActive(): boolean {
  return isLivingProxyWindow() && $responseMode.get() === 'voice' && !$screenLocked.get()
}

function canAutoPlayVoiceBar(): boolean {
  return isLivingVoiceBarActive() && $surfaceOpen.get() === 'living'
}

function hasOutstandingVoice(exceptId?: string): boolean {
  if ($voiceBarPlayingId.get() && $voiceBarPlayingId.get() !== exceptId) {
    return true
  }

  if (autoPlayQueue.some(id => id !== exceptId)) {
    return true
  }

  return Object.entries($chatMessageBodies.get()).some(
    ([id, body]) => id !== exceptId && body?.voiceStatus === 'pending'
  )
}

function failPendingVoiceBars(): void {
  for (const [id, body] of Object.entries($chatMessageBodies.get())) {
    if (body?.voiceStatus === 'pending' && body.voiceDuration == null) {
      $chatMessageBodies.setKey(id, { ...body, voiceStatus: undefined })
    }
  }
}

export function setTurnPendingEmotion(affect: { actions: string[]; emotion: SpriteEmotion } | null): void {
  turnPendingEmotion = affect
}

function setCachedVoiceAudio(messageId: string, dataUrl: string): void {
  audioDataUrlCache.set(messageId, dataUrl)
}

function getCachedVoiceAudio(messageId: string): string | undefined {
  return audioDataUrlCache.get(messageId)
}

function clearCachedVoiceAudio(): void {
  audioDataUrlCache.clear()
}

function applyTurnEndState(): void {
  if (turnPendingEmotion) {
    const affect = turnPendingEmotion
    turnPendingEmotion = null
    setSpriteState('emotional', { action: affect.actions[0], emotion: affect.emotion })
    playSpriteActionSequence(affect.actions)

    return
  }

  if ($spriteState.get() === 'speaking' || $spriteState.get() === 'thinking') {
    setSpriteState('idle', { force: true })
  }
}

function playNextOrFinish(): void {
  if (autoPlayQueue.length > 0) {
    const nextId = autoPlayQueue.shift()!
    void playVoiceBar(nextId)

    return
  }

  applyTurnEndState()
}

function measureAudioDuration(dataUrl: string): Promise<number> {
  return new Promise(resolve => {
    const audio = new Audio()
    audio.preload = 'metadata'

    let timer: ReturnType<typeof setTimeout> | null = null
    let settled = false

    const finish = (seconds: number): void => {
      if (settled) {
        return
      }

      settled = true

      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }

      audio.removeEventListener('loadedmetadata', onLoaded)
      audio.removeEventListener('error', onError)
      audio.removeAttribute('src')
      audio.load()
      resolve(seconds)
    }

    const secondsFrom = (raw: number): number => {
      if (!Number.isFinite(raw) || raw <= 0) {
        return 1
      }

      return Math.max(1, Math.round(raw))
    }

    const onLoaded = (): void => {
      finish(secondsFrom(audio.duration))
    }

    const onError = (): void => {
      finish(1)
    }

    timer = setTimeout(() => {
      finish(1)
    }, 4000)

    audio.addEventListener('loadedmetadata', onLoaded)
    audio.addEventListener('error', onError)
    audio.src = dataUrl
  })
}

function updateMessageVoice(messageId: string, patch: Partial<ChatMessageBody>): void {
  const current = $chatMessageBodies.get()[messageId]

  if (current) {
    $chatMessageBodies.setKey(messageId, { ...current, ...patch })
  }
}

export async function synthesizeVoiceBar(
  messageId: string,
  text: string,
  options?: { autoPlay?: boolean }
): Promise<void> {
  const trimmed = text.trim()

  if (!trimmed) {
    return
  }

  if (trimmed.length > TTS_MAX_TEXT_CHARS) {
    updateMessageVoice(messageId, { voiceStatus: 'failed' })

    if (!hasOutstandingVoice(messageId)) {
      applyTurnEndState()
    }

    return
  }

  const epoch = synthEpoch
  updateMessageVoice(messageId, { voiceStatus: 'pending' })

  try {
    const dataUrl = await requestSynth(trimmed, undefined, 'chat.replay', true)

    if (epoch !== synthEpoch) {
      return
    }

    const duration = await measureAudioDuration(dataUrl)

    if (epoch !== synthEpoch) {
      return
    }

    setCachedVoiceAudio(messageId, dataUrl)
    updateMessageVoice(messageId, { voiceDuration: duration, voiceStatus: 'ready' })

    if (options?.autoPlay && canAutoPlayVoiceBar()) {
      if ($voiceBarPlayingId.get() === null) {
        void playVoiceBar(messageId)
      } else if (!autoPlayQueue.includes(messageId)) {
        autoPlayQueue.push(messageId)
      }
    } else if (!hasOutstandingVoice(messageId)) {
      applyTurnEndState()
    }
  } catch (err) {
    console.warn('[chat-voice-bar] synthesis failed:', err)

    if (epoch === synthEpoch) {
      updateMessageVoice(messageId, { voiceStatus: 'failed' })

      if (!hasOutstandingVoice(messageId)) {
        applyTurnEndState()
      }
    }
  }
}

async function playVoiceBar(messageId: string, isManualClick = false): Promise<void> {
  if ($screenLocked.get()) {
    return
  }

  if (isManualClick) {
    autoPlayQueue.length = 0
    turnPendingEmotion = null
  }

  if ($voiceBarPlayingId.get() === messageId) {
    stopVoiceBar()
    turnPendingEmotion = null

    return
  }

  stopVoiceBar()

  const playToken = ++activePlayToken
  let dataUrl = getCachedVoiceAudio(messageId)

  if (!dataUrl) {
    const body = $chatMessageBodies.get()[messageId]
    const text = body?.text.trim()

    if (!text || text.length > TTS_MAX_TEXT_CHARS) {
      updateMessageVoice(messageId, { voiceStatus: 'failed' })

      return
    }

    $voiceBarLoadingId.set(messageId)

    try {
      dataUrl = await requestSynth(text, undefined, 'chat.replay', true)

      if (playToken !== activePlayToken) {
        return
      }

      const duration = await measureAudioDuration(dataUrl)

      if (playToken !== activePlayToken) {
        return
      }

      setCachedVoiceAudio(messageId, dataUrl)
      updateMessageVoice(messageId, { voiceDuration: duration, voiceStatus: 'ready' })
    } catch (err) {
      console.warn('[chat-voice-bar] click synth failed:', err)

      if (playToken === activePlayToken) {
        updateMessageVoice(messageId, { voiceStatus: 'failed' })
      }

      return
    } finally {
      if (playToken === activePlayToken) {
        $voiceBarLoadingId.set(null)
      }
    }
  }

  if (playToken !== activePlayToken) {
    return
  }

  $voiceBarPlayingId.set(messageId)
  setSpriteState('speaking')

  const onDone = (): void => {
    if (playToken === activePlayToken && $voiceBarPlayingId.get() === messageId) {
      $voiceBarPlayingId.set(null)
      playNextOrFinish()
    }
  }

  const ok = await playDataUrl(dataUrl, onDone)

  if (!ok && playToken === activePlayToken && $voiceBarPlayingId.get() === messageId) {
    $voiceBarPlayingId.set(null)
    playNextOrFinish()
  }
}

function stopVoiceBar(): void {
  activePlayToken++
  $voiceBarPlayingId.set(null)
  $voiceBarLoadingId.set(null)
  stopAudio()

  if ($spriteState.get() === 'speaking') {
    setSpriteState('idle', { force: true })
  }
}

export function cancelVoiceBar(): void {
  activePlayToken++
  synthEpoch++
  autoPlayQueue.length = 0
  turnPendingEmotion = null
  $voiceBarPlayingId.set(null)
  $voiceBarLoadingId.set(null)
  stopAudio()
  failPendingVoiceBars()

  if ($spriteState.get() === 'speaking' || $spriteState.get() === 'thinking') {
    setSpriteState('idle', { force: true })
  }
}

$screenLocked.listen(locked => {
  if (locked) {
    cancelVoiceBar()
  }
})

$responseMode.listen(mode => {
  if (mode !== 'voice') {
    cancelVoiceBar()
  }
})

registerStorageClearHandler(() => {
  clearCachedVoiceAudio()
  cancelVoiceBar()
})

interface ChatVoiceBarProps {
  duration?: number
  messageId: string
}

export function ChatVoiceBar({ duration, messageId }: ChatVoiceBarProps): React.JSX.Element {
  const playingId = useStore($voiceBarPlayingId)
  const loadingId = useStore($voiceBarLoadingId)

  const isPlaying = playingId === messageId
  const isLoading = loadingId === messageId

  const sec = duration ? Math.max(1, Math.min(60, duration)) : 1
  const widthPx = duration ? 72 + Math.round(((sec - 1) / 59) * (220 - 72)) : 72

  const handleClick = (e: React.MouseEvent): void => {
    e.stopPropagation()
    void playVoiceBar(messageId, true)
  }

  return (
    <button
      aria-label={isPlaying ? '停止播放语音' : '播放语音'}
      className={cn(
        'group/voicebar relative inline-flex items-center justify-between rounded-2xl px-3.5 py-2 text-xs backdrop-blur-md transition select-none cursor-pointer',
        'border border-line-hairline bg-surface-card/20 text-strong hover:bg-surface-card/40',
        isPlaying && 'bg-accent-soft/40 border-accent-line/50'
      )}
      onClick={handleClick}
      style={{ width: `${widthPx}px` }}
      type="button"
    >
      <div className="flex items-center gap-1.5">
        {isLoading ? (
          <Loader2 className="size-3.5 shrink-0 text-accent animate-spin" />
        ) : isPlaying ? (
          <Volume2 className="size-3.5 shrink-0 text-accent animate-pulse" />
        ) : (
          <Volume2 className="size-3.5 shrink-0 text-strong/70 group-hover/voicebar:text-strong transition-colors" />
        )}
      </div>
      {typeof duration === 'number' && duration > 0 ? (
        <span
          className={cn(
            'text-[11px] font-medium tracking-tight',
            isPlaying ? 'text-accent font-semibold' : 'text-faint'
          )}
        >
          {duration}″
        </span>
      ) : null}
    </button>
  )
}

interface TranscriptBlockProps {
  text: string
}

export function TranscriptBlock({ text }: TranscriptBlockProps): React.JSX.Element | null {
  const [expanded, setExpanded] = useState(false)
  const trimmed = text.trim()

  if (!trimmed) {
    return null
  }

  return (
    <div className="mt-1.5 flex max-w-full flex-col select-none">
      <button
        aria-expanded={expanded}
        className={cn(
          'group/transcript inline-flex items-center gap-1.5 rounded-lg border border-line-hairline/60 bg-surface-card/20 px-2.5 py-1 text-[11px] text-faint backdrop-blur-xs transition-all duration-150',
          'hover:border-line-standard hover:bg-surface-card/40 hover:text-strong cursor-pointer text-left'
        )}
        onClick={() => setExpanded(prev => !prev)}
        type="button"
      >
        <FileText className="size-3 shrink-0 text-faint group-hover/transcript:text-strong transition-colors" />
        <span className="font-medium text-strong/80">文字稿</span>
        <span className="text-[10px] text-faint group-hover/transcript:text-strong/70 transition-colors ml-0.5">
          {expanded ? '收起' : '展开查看'}
        </span>
        <ChevronDown
          className={cn(
            'size-3 shrink-0 text-faint transition-transform duration-200 group-hover/transcript:text-strong',
            expanded ? 'rotate-180' : '-rotate-90'
          )}
        />
      </button>
      {expanded ? (
        <div className="mt-1.5 max-h-60 max-w-full overflow-y-auto rounded-xl border border-line-hairline bg-surface-card/30 p-3 text-xs leading-relaxed text-strong shadow-inner backdrop-blur-md select-text cursor-text whitespace-pre-wrap break-words font-sans">
          {trimmed}
        </div>
      ) : null}
    </div>
  )
}
