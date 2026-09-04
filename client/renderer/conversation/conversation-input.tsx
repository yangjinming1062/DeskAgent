// 对话输入胶囊：两个入口共用。空胶囊形态；聚焦或挂附件时由 caller 决定是否展开指挥台。
//
// 此组件是受控组件：父组件持有 text/pending/sending/recording 等状态，
// 这里只渲染 + 把事件转回父组件。这样 living / workbench 可以共用同一个
// 视觉与交互壳，而父组件可以各自选择是否挂语音条、附件槽、slash popover。

import type React from 'react'
import {
  type ClipboardEvent,
  type Dispatch,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
  type SetStateAction,
  useState
} from 'react'

import { attachVideoFile, pickFile, pickFolder, pickImage, pickVideo } from '@/chat/chat-attach-picker'
import { PendingAttachmentView } from '@/chat/chat-pending-attachment'
import type { PendingAttachment } from '@/chat/chat-store'
import { SlashCommandPopover } from '@/chat/slash-command-popover'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { ArrowRight, FileText, FolderOpen, ImageIcon, Mic, Plus, SquareFilled, Video } from '@/shared/lib/icons'
import { fuzzyFilterCommands, type ScoredSlashCommand, type SlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { strings } from '@/shared/strings'

export interface ChatSubmitState {
  gatewayState: ConnectionState
  isGenerating: boolean
  isReadOnlySession: boolean
  pending: PendingAttachment | null
  recording: boolean
  sending: boolean
  text: string
}

export interface SlashState {
  highlightIndex?: number
  items?: ScoredSlashCommand[]
  onHighlight?: (index: number) => void
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement>) => void
  onSelect?: (cmd: SlashCommandMeta, args: string[]) => void
  popoverOpen?: boolean
  query?: string
}

export interface ConversationInputProps {
  attachMenuOpen?: boolean
  externalPathsRef: RefObject<string[]>
  inputRef: RefObject<HTMLInputElement | null>
  onAttachMenuToggle?: Dispatch<SetStateAction<boolean>>
  onDrop?: (e: React.DragEvent) => void
  onPaste?: (e: ClipboardEvent) => void | Promise<void>
  onRecordingPointerCancel?: (e: PointerEvent<HTMLButtonElement>) => void
  onRecordingPointerDown?: (e: PointerEvent<HTMLButtonElement>) => void
  onRecordingPointerUp?: (e: PointerEvent<HTMLButtonElement>) => void
  onSend: () => void
  onSetPending: Dispatch<SetStateAction<PendingAttachment | null>>
  onSetText: (next: string) => void
  onStop: () => void
  onTyping?: () => void
  setAttachMenuRef: RefObject<HTMLDivElement | null>
  slash?: SlashState
  submit: ChatSubmitState
}

export function ConversationInput(props: ConversationInputProps): React.JSX.Element {
  const {
    attachMenuOpen = false,
    externalPathsRef,
    inputRef,
    onAttachMenuToggle,
    onDrop,
    onPaste,
    onRecordingPointerCancel,
    onRecordingPointerDown,
    onRecordingPointerUp,
    onSend,
    onSetPending,
    onSetText,
    onStop,
    onTyping,
    setAttachMenuRef,
    slash,
    submit
  } = props

  const { gatewayState, isGenerating, isReadOnlySession, pending, recording, sending, text } = submit

  const {
    highlightIndex: slashHighlightIndex,
    items: slashItems,
    onHighlight: onSlashHighlight,
    onKeyDown: onSlashKeyDown,
    onSelect: onSlashSelect,
    popoverOpen: slashPopoverOpen,
    query: slashQuery
  } = slash ?? {}

  const [internalSlashDismissed, setInternalSlashDismissed] = useState(false)
  const [internalHighlightIndex, setInternalHighlightIndex] = useState(0)

  const derivedSlashQuery = (() => {
    if (slashQuery !== undefined) {
      return slashQuery
    }

    const trimmed = text.trim()

    if (!trimmed.startsWith('/')) {
      return ''
    }

    const body = trimmed.slice(1)
    const spaceIdx = body.search(/\s/)

    return spaceIdx === -1 ? body : ''
  })()

  const items = slashItems ?? (derivedSlashQuery ? fuzzyFilterCommands(derivedSlashQuery, 8) : [])
  const isOpen = (slashPopoverOpen ?? (derivedSlashQuery.length > 0 && !internalSlashDismissed)) && items.length > 0
  const highlightIdx = slashHighlightIndex ?? internalHighlightIndex

  const handleSlashSelect = (cmd: SlashCommandMeta): void => {
    if (onSlashSelect) {
      onSlashSelect(cmd, [])
    } else {
      onSetText(`/${cmd.name} `)
      inputRef.current?.focus()
    }

    setInternalSlashDismissed(true)
  }

  const sendDisabled =
    isReadOnlySession ||
    (!isGenerating &&
      (sending ||
        gatewayState !== 'open' ||
        (!text.trim() && !pending && externalPathsRef.current.length === 0) ||
        (pending?.type === 'video' && pending.status !== 'ready')))

  return (
    <div
      className="border-t border-line-standard p-2.5 flex flex-col gap-1.5 shrink-0 bg-surface-chrome/20"
      onDragOver={onDrop ? e => e.preventDefault() : undefined}
      onDrop={onDrop}
    >
      {gatewayState !== 'open' && <p className="text-center text-[10px] text-amber-300/80">正在连接…</p>}
      {isReadOnlySession && <p className="text-center text-[10px] text-faint">IM 对话 · 只读</p>}

      {pending && (
        <div className="px-1">
          <PendingAttachmentView
            onRemove={() => onSetPending(null)}
            onRetry={pending.type === 'video' ? () => void attachVideoFile(pending.path, onSetPending) : undefined}
            pending={pending}
            sending={sending}
          />
        </div>
      )}

      <div className="relative flex h-10 w-full items-center gap-1.5 rounded-full border border-line-standard bg-fill-faint px-2 transition focus-within:border-accent/60 focus-within:bg-fill-hover shadow-xs">
        {isOpen && (
          <SlashCommandPopover
            highlightedIndex={highlightIdx}
            onHighlight={idx => {
              setInternalHighlightIndex(idx)
              onSlashHighlight?.(idx)
            }}
            onSelect={cmd => handleSlashSelect(cmd)}
            query={derivedSlashQuery}
          />
        )}

        <div className="relative shrink-0" ref={setAttachMenuRef}>
          <button
            aria-label="添加附件"
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-full text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40',
              attachMenuOpen && 'bg-fill-hover text-strong'
            )}
            disabled={isReadOnlySession}
            onClick={() => onAttachMenuToggle?.(!attachMenuOpen)}
            title="添加附件"
            type="button"
          >
            <Plus className="size-4" />
          </button>

          {attachMenuOpen && (
            <div className="absolute bottom-full mb-2 left-0 z-50 flex w-36 flex-col gap-0.5 rounded-xl border border-line-standard bg-surface-card p-1 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150">
              <button
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                onClick={() => void pickFile(onSetPending)}
                type="button"
              >
                <FileText className="size-3.5 text-accent" />
                <span>添加文件</span>
              </button>
              <button
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                onClick={() => void pickFolder(onSetPending)}
                type="button"
              >
                <FolderOpen className="size-3.5 text-amber-400" />
                <span>添加文件夹</span>
              </button>
              <button
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                onClick={() => void pickImage(onSetPending)}
                type="button"
              >
                <ImageIcon className="size-3.5 text-emerald-400" />
                <span>添加图片</span>
              </button>
              <button
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                onClick={() => void pickVideo(onSetPending)}
                type="button"
              >
                <Video className="size-3.5 text-rose-400" />
                <span>添加视频</span>
              </button>
            </div>
          )}
        </div>

        <input
          className="h-full flex-1 bg-transparent border-0 outline-none text-xs text-strong placeholder:text-faint px-1.5"
          disabled={isReadOnlySession}
          onChange={e => {
            setInternalSlashDismissed(false)
            setInternalHighlightIndex(0)
            onSetText(e.target.value)
            onTyping?.()
          }}
          onKeyDown={e => {
            onSlashKeyDown?.(e)

            if (isOpen) {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                const next = (highlightIdx + 1) % items.length
                setInternalHighlightIndex(next)
                onSlashHighlight?.(next)

                return
              }

              if (e.key === 'ArrowUp') {
                e.preventDefault()
                const next = (highlightIdx - 1 + items.length) % items.length
                setInternalHighlightIndex(next)
                onSlashHighlight?.(next)

                return
              }

              if (e.key === 'Tab') {
                e.preventDefault()
                const chosen = items[highlightIdx]

                if (chosen) {
                  handleSlashSelect(chosen.cmd)
                }

                return
              }

              if (e.key === 'Escape') {
                e.preventDefault()
                setInternalSlashDismissed(true)

                return
              }

              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                const chosen = items[highlightIdx]

                if (chosen && derivedSlashQuery.length > 0) {
                  e.preventDefault()
                  handleSlashSelect(chosen.cmd)

                  return
                }
              }
            }

            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              onSend()
            }
          }}
          onPaste={onPaste}
          placeholder={strings.chat.inputPlaceholder}
          ref={inputRef}
          type="text"
          value={text}
        />

        <div className="flex items-center gap-1 shrink-0">
          <button
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-full transition disabled:pointer-events-none disabled:opacity-40',
              recording
                ? 'border border-rose-400/70 bg-rose-500/25 text-rose-200 animate-pulse'
                : 'text-muted hover:bg-fill-hover hover:text-strong'
            )}
            disabled={isReadOnlySession}
            onPointerCancel={onRecordingPointerCancel}
            onPointerDown={onRecordingPointerDown}
            onPointerUp={onRecordingPointerUp}
            title={recording ? '松开发送语音' : '按住录制语音消息'}
            type="button"
          >
            <Mic className="size-3.5" />
          </button>

          <button
            aria-label={isGenerating ? '停止生成' : '发送消息'}
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-full transition disabled:pointer-events-none disabled:opacity-30',
              isGenerating
                ? 'bg-rose-500/90 hover:bg-rose-600 text-on-accent shadow-xs'
                : 'bg-accent hover:bg-accent/85 text-on-accent shadow-xs'
            )}
            disabled={sendDisabled}
            onClick={() => void (isGenerating ? onStop() : onSend())}
            title={isGenerating ? '停止生成' : '发送消息 (Enter)'}
            type="button"
          >
            {isGenerating ? <SquareFilled className="size-3" /> : <ArrowRight className="size-3.5" />}
          </button>
        </div>
      </div>
    </div>
  )
}
