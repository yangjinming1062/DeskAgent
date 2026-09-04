import { useCallback, useRef, useState } from 'react'

import { cancelAutoVoice, setSpriteState } from '@/companion'
import { useGatewayRequest } from '@/shared'
import { parseSlashInput } from '@/shared/lib/slash-commands'
import { notify } from '@/shared/store/notifications'
import type { ChatAttachment } from '@/shared/types/spiritagent'

import { basename } from './chat-path'
import { executeSlashCommand, slashPreCheck } from './chat-slash'
import {
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $chatTurnInFlight,
  cancelPendingFlush,
  finalizeAssistantMessage,
  markAssistantTerminal,
  type PendingAttachment,
  pushPendingPrompt,
  pushUserMessage,
  schedulePendingFlush,
  submitPendingBatch
} from './chat-store'
import { ensureChatSession } from './session-list-store'

interface UseChatSubmitOptions {
  externalPathsRef: React.MutableRefObject<string[]>
  gatewayState: string
  isReadOnlySession: boolean
  onClearExternalPaths: () => void
  onPreCheckFail: (message: string) => void
}

export interface ChatSubmit {
  handleStop: () => Promise<void>
  pending: PendingAttachment | null
  sending: boolean
  send: () => Promise<void>
  setPending: React.Dispatch<React.SetStateAction<PendingAttachment | null>>
  setSending: React.Dispatch<React.SetStateAction<boolean>>
  setText: React.Dispatch<React.SetStateAction<string>>
  text: string
}

export function useChatSubmit({
  externalPathsRef,
  gatewayState,
  isReadOnlySession,
  onClearExternalPaths,
  onPreCheckFail
}: UseChatSubmitOptions): ChatSubmit {
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pending, setPending] = useState<PendingAttachment | null>(null)
  const [sending, setSending] = useState(false)

  // 通过 ref 转发最新值给 send（避免 useCallback 依赖列表频繁变更）。
  const textRef = useRef(text)
  const pendingRef = useRef(pending)
  const sendingRef = useRef(sending)
  textRef.current = text
  pendingRef.current = pending
  sendingRef.current = sending

  const send = useCallback(async () => {
    const currentText = textRef.current
    const currentPending = pendingRef.current
    const currentSending = sendingRef.current

    if (isReadOnlySession) {
      return
    }

    const trimmed = currentText.trim()

    if (currentSending) {
      return
    }

    if (currentPending?.type === 'video' && currentPending.status !== 'ready') {
      notify({
        durationMs: 3000,
        kind: currentPending.status === 'error' ? 'error' : 'info',
        message: currentPending.status === 'error' ? '视频上传失败，请重新选择' : '视频正在上传处理中，请稍候…'
      })

      return
    }

    if (!trimmed && !currentPending) {
      return
    }

    if (gatewayState !== 'open') {
      return
    }

    const parsed = parseSlashInput(trimmed)

    if (parsed) {
      const preCheck = slashPreCheck(currentPending, currentSending)

      if (preCheck) {
        onPreCheckFail(preCheck)

        return
      }

      if (parsed.command) {
        await executeSlashCommand(parsed.command, parsed.args, {
          onFinish: () => setSending(false),
          onStart: () => {
            setSending(true)
            setText('')
          },
          requestGateway
        })

        return
      }

      onPreCheckFail(`未知命令: /${parsed.name}。试试 /help 查看可用命令。`)

      return
    }

    setSending(true)

    try {
      const id = await ensureChatSession()
      let fullText = trimmed
      let promptText = trimmed
      const attachments: ChatAttachment[] = []
      const displayAttachments: ChatAttachment[] = []

      if (currentPending?.type === 'image') {
        // 本地图片优先以 data URL 附件直发多模态（后端转 input_image parts，视觉链路接手）；
        // 读取失败（不可读/超体量）才降级路径模式：@file: 指令进正文，LLM 走文件工具读取。
        let dataUrl: string | null = currentPending.value.startsWith('data:') ? currentPending.value : null

        if (!dataUrl) {
          try {
            dataUrl = await window.spiritagent.readImageForAttach(currentPending.value)
          } catch {
            /* 降级路径模式 */
          }
        }

        if (dataUrl) {
          attachments.push({ type: 'image', url: dataUrl })
          displayAttachments.push({ type: 'image', url: dataUrl })
        } else {
          const ref = await requestGateway<{ ref_text?: string }>('image.attach', {
            path: currentPending.value,
            session_id: id
          })

          if (ref.ref_text) {
            fullText = `${fullText}\n${ref.ref_text}`.trim()
            promptText = `${promptText}\n${ref.ref_text}`.trim()
            displayAttachments.push({ type: 'image', url: currentPending.value })
          }
        }
      } else if (currentPending?.type === 'video' && currentPending.url) {
        attachments.push({ type: 'video', url: currentPending.url })
        displayAttachments.push({ type: 'video', url: currentPending.url })
      } else if (currentPending?.type === 'file') {
        const fileRef = `[文件: ${currentPending.fileName}] ${currentPending.path}`
        fullText = fullText ? `${fullText}\n${fileRef}` : fileRef
        const fileDirective = `@file:${currentPending.path}`
        promptText = promptText ? `${promptText}\n${fileDirective}` : fileDirective
      } else if (currentPending?.type === 'folder') {
        const folderRef = `[文件夹: ${currentPending.folderName}] ${currentPending.path}`
        fullText = fullText ? `${fullText}\n${folderRef}` : folderRef
        const folderDirective = `@folder:${currentPending.path}`
        promptText = promptText ? `${promptText}\n${folderDirective}` : folderDirective
      }

      const extra = externalPathsRef.current

      if (extra.length > 0) {
        const names = extra.map(basename).join('、')
        fullText = fullText ? `${fullText}\n附件：${names}` : `附件：${names}`
        const extraDirectives = extra.map(p => `@file:${p}`).join('\n')
        promptText = promptText ? `${promptText}\n${extraDirectives}` : extraDirectives
      }

      externalPathsRef.current = []
      onClearExternalPaths()

      // 展示层：媒体卡即内容，纯附件消息不留占位文案；仅附件与正文全空时兜底。
      const displayPlaceholder = displayAttachments.length
        ? ''
        : currentPending?.type === 'video'
          ? '（视频）'
          : currentPending?.type === 'image'
            ? '（图片）'
            : currentPending?.type === 'file'
              ? `[文件] ${currentPending.fileName}`
              : currentPending?.type === 'folder'
                ? `[文件夹] ${currentPending.folderName}`
                : ''

      const promptFallback =
        currentPending?.type === 'video'
          ? '请看这段视频'
          : currentPending?.type === 'image'
            ? '请看这张图片'
            : currentPending?.type === 'file'
              ? `@file:${currentPending.path}`
              : currentPending?.type === 'folder'
                ? `@folder:${currentPending.path}`
                : ''

      pushUserMessage(fullText || displayPlaceholder, displayAttachments.length ? displayAttachments : undefined)
      setText('')
      setPending(null)
      setSpriteState('thinking')

      pushPendingPrompt({
        attachments: attachments.length ? attachments : undefined,
        text: promptText || promptFallback
      })
      schedulePendingFlush()
    } catch (err) {
      markAssistantTerminal({ error: err instanceof Error ? err.message : '发送失败' })
      setSpriteState('idle')
      setPending(null)
    } finally {
      setSending(false)
    }
  }, [externalPathsRef, gatewayState, isReadOnlySession, onClearExternalPaths, onPreCheckFail, requestGateway])

  const handleStop = useCallback(async () => {
    cancelAutoVoice()
    cancelPendingFlush()
    $chatTurnInFlight.set(false)
    const sid = $chatSessionId.get()

    if (sid) {
      try {
        await requestGateway('session.interrupt', { session_id: sid })
      } catch {
        /* 尽力而为 */
      }
    }

    void window.spiritagent?.runnerCancel?.().catch(() => {})

    const lastItem = $chatMessageList.get().at(-1)
    const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

    if (lastItem?.role === 'assistant' && lastBody?.streaming && lastBody.text.trim()) {
      finalizeAssistantMessage()
    } else {
      markAssistantTerminal({ cancelled: true })
    }

    setSpriteState('idle', { force: true })
    submitPendingBatch()
  }, [requestGateway])

  return {
    handleStop,
    pending,
    send,
    sending,
    setPending,
    setSending,
    setText,
    text
  }
}
