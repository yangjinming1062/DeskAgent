import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { notifyError } from '@/shared/store/notifications'

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
    // stopSpeaking → audio-track.stopAudio → 同步触发旧 onDone 清 atom；但若
    // TTS 合成仍在 IPC 途中（还没有 onDone 可触发），必须在这里直接清。
    if (currentPlayingId === messageId) {
      stopSpeaking()
      $chatPlaybackId.set(null)

      return
    }

    // 场景 B：抢占。先停掉旧播放（同步触发旧 onDone → 清旧 atom），再占新槽位。
    if (currentPlayingId !== null) {
      stopSpeaking()
    }

    $chatPlaybackId.set(messageId)

    // 点播历史消息不驱动说话状态——徽标只反映伙伴正在回复（流式 / 自动语音）；
    // 口型同步由 audio-track 的音频振幅直驱，与精灵状态无关。
    // myDone 由 audio-track 在 ended / error / stopAudio 三种路径上同步触发。
    // 只在它仍指向当前 messageId 时清空 atom，防止"老 onDone 误杀新播放"。
    const myDone = () => {
      if ($chatPlaybackId.get() === messageId) {
        $chatPlaybackId.set(null)
      }
    }

    try {
      await speakChatMessage(text, undefined, myDone)
    } catch (err) {
      // audio-track.stopAudio 已经在 synth() 的 catch 里调用；这里清状态并让用户看见失败——
      // 显式点了播放却无声回到 idle，用户无从得知语音服务已故障。
      if ($chatPlaybackId.get() === messageId) {
        $chatPlaybackId.set(null)
        notifyError(err, '语音朗读失败')
      }
    }
  }

  let label = '播放'
  let icon = '🔊'

  let styleClass =
    'rounded-full border border-line-strong bg-fill-faint px-2 py-1 text-xs text-muted transition hover:bg-fill-hover hover:text-strong'

  if (isMinePlaying) {
    label = '停止播放'
    styleClass =
      'rounded-full border border-line-strong bg-fill-hover px-2 py-1 text-xs text-strong transition hover:bg-fill-hover'
  } else if (voicePreparing && otherBusy) {
    icon = '⏳'
    label = '正在准备语音'
    styleClass =
      'rounded-full border border-line-standard bg-fill-faint px-2 py-1 text-xs text-faint cursor-not-allowed'
  } else if (voicePreparing) {
    // 自己正在准备（gen 已占但 playDataUrl 还没 resolve）—— 显示 loading。
    label = '正在准备语音'
    styleClass =
      'rounded-full border border-line-strong bg-fill-faint px-2 py-1 text-xs text-faint cursor-progress animate-pulse'
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
