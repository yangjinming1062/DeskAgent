import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { speakChatMessage, stopSpeaking } from './tts'
import { $voicePreparing } from './voice-state'

/** TTS 播放按钮，跟随每一条已完成的精灵回复消息展示。点击切换「播放 / 停止」。
 *
 *  设计要点：
 *  - 同一条消息的按钮第二次点击 = 停止（而不是"暂停+续播"，见 audio-track.ts 设计）。
 *  - 不同消息的按钮之间通过模块级单例抢占，旧的按钮立刻回到 idle。
 *  - TTS 调用统一走 `speakChatMessage`，永远 `persist: true`，命中磁盘缓存。
 *
 *  当前正在播放的 messageId 用一个 nanostores atom 持有，这样组件能通过
 *  `useStore` 订阅、抢占 / 结束时自动重新渲染——模块级普通变量在 React 看来
 *  是不可见的。
 */

interface ChatMessagePlayButtonProps {
  text: string
  messageId: string
  className?: string
}

const $chatPlaybackId = atom<string | null>(null)

export function ChatMessagePlayButton({
  text,
  messageId,
  className = ''
}: ChatMessagePlayButtonProps): React.JSX.Element {
  const voicePreparing = useStore($voicePreparing)
  const currentPlayingId = useStore($chatPlaybackId)

  const isMinePlaying = currentPlayingId === messageId
  // 其他消息在加载/播放时，当前按钮整体禁用，避免竞速点击。
  const otherBusy = voicePreparing && !isMinePlaying

  const onClick = async (): Promise<void> => {
    // 场景 A：当前按钮就是正在播放/准备中的那一条 → 停止（toggle-stop）。
    // stopSpeaking → audio-track.stopAudio → 触发我们之前传给 playDataUrl 的
    // onDone（如果该 onDone 仍然绑定到当前 messageId，会把 atom 清空）。
    if (currentPlayingId === messageId) {
      stopSpeaking()

      return
    }

    // 场景 B：抢占。先停掉旧播放（同步触发旧 onDone → 清旧 atom），再占新槽位。
    if (currentPlayingId !== null) {
      stopSpeaking()
    }

    $chatPlaybackId.set(messageId)

    // myDone 由 audio-track 在 ended / error / stopAudio 三种路径上同步触发。
    // 只在它仍指向当前 messageId 时清空 atom，防止"老 onDone 误杀新播放"。
    const myDone = () => {
      if ($chatPlaybackId.get() === messageId) {
        $chatPlaybackId.set(null)
      }
    }

    try {
      await speakChatMessage(text, undefined, myDone)
    } catch {
      // audio-track.stopAudio 已经在 synth() 的 catch 里调用；这里只兜底清状态。
      if ($chatPlaybackId.get() === messageId) {
        $chatPlaybackId.set(null)
      }
    }
  }

  let label = '播放'
  let icon = '🔊'

  let styleClass =
    'rounded-full border border-white/20 bg-white/5 px-2 py-1 text-xs text-white/50 transition hover:bg-white/15 hover:text-white'

  if (isMinePlaying) {
    label = '停止播放'
    styleClass =
      'rounded-full border border-white/30 bg-white/10 px-2 py-1 text-xs text-white transition hover:bg-white/20'
  } else if (voicePreparing && otherBusy) {
    icon = '⏳'
    label = '正在准备语音'
    styleClass = 'rounded-full border border-white/10 bg-white/5 px-2 py-1 text-xs text-white/20 cursor-not-allowed'
  } else if (voicePreparing) {
    // 自己正在准备（gen 已占但 playDataUrl 还没 resolve）—— 显示 loading。
    label = '正在准备语音'
    styleClass =
      'rounded-full border border-white/20 bg-white/5 px-2 py-1 text-xs text-white/30 cursor-progress animate-pulse'
  }

  return (
    <button
      aria-label={label}
      className={`${styleClass} ${className}`}
      disabled={Boolean(otherBusy)}
      onClick={() => {
        void onClick()
      }}
      title={label}
      type="button"
    >
      <span aria-hidden="true">{icon}</span>
    </button>
  )
}

// 仅供测试使用：每次测试 case 开始时重置模块级状态。
export function __resetChatPlayButtonStateForTests(): void {
  $chatPlaybackId.set(null)
  $voicePreparing.set(false)
}
