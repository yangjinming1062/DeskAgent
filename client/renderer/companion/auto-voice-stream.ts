import { $screenLocked } from '@/companion/activity'
import { $spriteState, setSpriteState } from '@/companion/companion-store'

import { playDataUrl, stopAudio } from './audio-track'
import { SentenceSegmenter } from './speech-segmenter'
import { synthAudio } from './tts'
import { beginVoicePreparing, endVoicePreparing } from './voice-state'

/**
 * 始终语音模式的句级流式编排队列。
 *
 * 增量接收 message.delta 切句，单句预取并发合成，首句就绪即起播。
 * 遵循单槽抢占语义：外部播放/打断/来电/锁屏使当前句停止，队列见 false/token失效即刻退出。
 */

const MAX_PENDING_SEGMENTS = 8

interface PrefetchItem {
  text: string
  promise: Promise<string | null>
}

interface AutoVoiceRun {
  token: number
  segmenter: SentenceSegmenter
  pending: string[]
  closed: boolean
  playing: boolean
  refHeld: boolean
  wake: (() => void) | null
  prefetched: PrefetchItem | null
  synth: (text: string) => Promise<string | null>
}

let tokenSeq = 0
let currentRun: AutoVoiceRun | null = null

export function isAutoVoiceActive(): boolean {
  return currentRun !== null && !currentRun.closed
}

function trimBacklog(pending: string[]): void {
  if (pending.length <= MAX_PENDING_SEGMENTS) {
    return
  }

  const overflow = pending.length - MAX_PENDING_SEGMENTS
  const toMerge = pending.splice(0, overflow + 1)
  const merged = toMerge.join(' ')
  pending.unshift(merged)
}

function wakePump(run: AutoVoiceRun): void {
  if (run.wake) {
    const fn = run.wake
    run.wake = null
    fn()
  }
}

function maybePrefetch(run: AutoVoiceRun): void {
  if (run.playing && !run.prefetched && run.pending.length > 0) {
    const nextText = run.pending[0]
    run.prefetched = {
      text: nextText,
      promise: run.synth(nextText)
    }
  }
}

function enqueueSegments(run: AutoVoiceRun, segs: string[]): void {
  if (segs.length === 0) {
    return
  }

  run.pending.push(...segs)
  trimBacklog(run.pending)
  maybePrefetch(run)
  wakePump(run)
}

function finishRun(run: AutoVoiceRun): void {
  if (currentRun === run) {
    currentRun = null
  }

  if (run.refHeld) {
    endVoicePreparing()
    run.refHeld = false
  }

  if ($spriteState.get() === 'speaking') {
    setSpriteState('idle', { force: true })
  }
}

export function cancelAutoVoice(): void {
  if (!currentRun) {
    return
  }

  const run = currentRun
  currentRun = null
  run.closed = true
  run.pending = []
  stopAudio()
  wakePump(run)

  if (run.refHeld) {
    endVoicePreparing()
    run.refHeld = false
  }

  if ($spriteState.get() === 'speaking') {
    setSpriteState('idle', { force: true })
  }
}

export function beginAutoVoiceTurn(): void {
  cancelAutoVoice()

  const run: AutoVoiceRun = {
    token: ++tokenSeq,
    segmenter: new SentenceSegmenter(),
    pending: [],
    closed: false,
    playing: false,
    refHeld: false,
    wake: null,
    prefetched: null,
    synth: (text: string) => {
      if (!run.refHeld) {
        beginVoicePreparing()
        run.refHeld = true
      }

      return synthAudio(text).then(
        url => url,
        err => {
          console.warn('[auto-voice] segment synth failed, skipping', err)

          return null
        }
      )
    }
  }

  currentRun = run
  void pump(run)
}

export function feedAutoVoiceDelta(delta: string): void {
  if (!currentRun || currentRun.closed) {
    return
  }

  enqueueSegments(currentRun, currentRun.segmenter.feed(delta))
}

export function flushAutoVoiceSegments(): void {
  if (!currentRun || currentRun.closed) {
    return
  }

  enqueueSegments(currentRun, currentRun.segmenter.flush())
}

export function endAutoVoiceTurn(): void {
  if (!currentRun || currentRun.closed) {
    return
  }

  enqueueSegments(currentRun, currentRun.segmenter.flush())
  currentRun.closed = true
  wakePump(currentRun)
}

async function pump(run: AutoVoiceRun): Promise<void> {
  while (currentRun === run) {
    // 门控检查：锁屏状态中止后续播放
    if ($screenLocked.get()) {
      break
    }

    if (run.pending.length === 0) {
      if (run.closed) {
        break
      }

      await new Promise<void>(resolve => {
        run.wake = resolve
      })
      run.wake = null

      continue
    }

    const text = run.pending.shift()!

    if (!text) {
      continue
    }

    let dataUrl: string | null = null

    if (run.prefetched && run.prefetched.text === text) {
      dataUrl = await run.prefetched.promise
      run.prefetched = null
    } else {
      // 预取句与队首不符（积压合并改写过头部）——丢弃过期预取，
      // 否则它会永久占住预取槽，句间前瞻从此失效。
      run.prefetched = null
      dataUrl = await run.synth(text)
    }

    if (currentRun !== run) {
      break
    }

    if (!dataUrl) {
      // 合成失败降级跳过
      continue
    }

    // 准备起播前，若队列还有待播句且尚未预取，发起下一句前瞻合成
    if (run.pending.length > 0 && !run.prefetched) {
      const nextText = run.pending[0]
      run.prefetched = {
        text: nextText,
        promise: run.synth(nextText)
      }
    }

    run.playing = true
    setSpriteState('speaking')
    const ok = await playDataUrl(dataUrl)
    run.playing = false

    if (currentRun !== run || !ok) {
      // 被外部抢占、中止或播放异常
      break
    }
  }

  finishRun(run)
}
