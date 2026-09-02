import { useResolvedMediaSrc } from '@/companion/chat-media-src'
import type { PendingAttachment } from '@/companion/chat-store'
import { openMediaViewer } from '@/companion/media-viewer-overlay'
import { FileText, FolderOpen, Video, X } from '@/shared/lib/icons'

function PendingImageThumb({ path }: { path: string }): React.JSX.Element {
  const src = useResolvedMediaSrc({ type: 'image', url: path })

  return (
    <button
      className="block h-16 w-16 shrink-0 cursor-zoom-in overflow-hidden rounded-lg border border-line-standard bg-fill-trough p-0 transition hover:border-line-strong"
      onClick={() => openMediaViewer({ type: 'image', url: path })}
      type="button"
    >
      {src ? (
        <img alt="待发送图片" className="block h-full w-full object-cover" src={src} />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-[10px] text-faint">加载中…</span>
      )}
    </button>
  )
}

interface PendingAttachmentViewProps {
  onRemove: () => void
  onRetry?: () => void
  pending: PendingAttachment
  sending: boolean
}

export function PendingAttachmentView({
  onRemove,
  onRetry,
  pending,
  sending
}: PendingAttachmentViewProps): React.JSX.Element {
  if (pending.type === 'image') {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
        <PendingImageThumb path={pending.value} />
        <span className="truncate flex-1 text-[11px] text-body">
          {sending ? '图片发送中…' : pending.fileName || '已附加图片'}
        </span>
        {!sending && (
          <button
            aria-label="移除附加图片"
            className="rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
            onClick={onRemove}
            type="button"
          >
            <X className="size-3" />
          </button>
        )}
      </div>
    )
  }

  if (pending.type === 'video') {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
        <Video className="size-3.5 shrink-0 text-rose-400" />
        <span className="max-w-40 shrink truncate text-[11px] text-body">{pending.fileName}</span>
        {pending.status === 'uploading' && <span className="text-[10px] text-faint">上传中…</span>}
        {pending.status === 'ready' && <span className="text-[10px] text-emerald-400">已就绪</span>}
        {pending.status === 'error' && (
          <>
            <span className="min-w-0 flex-1 truncate text-[10px] text-amber-300/80" title={pending.error}>
              {pending.error}
            </span>
            {onRetry && (
              <button
                className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] text-muted transition hover:bg-fill-hover hover:text-strong"
                onClick={onRetry}
                type="button"
              >
                重试
              </button>
            )}
          </>
        )}
        {!sending && (
          <button
            aria-label="移除附加视频"
            className="shrink-0 rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
            onClick={onRemove}
            type="button"
          >
            <X className="size-3" />
          </button>
        )}
      </div>
    )
  }

  if (pending.type === 'file') {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
        <FileText className="size-3.5 shrink-0 text-accent" />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[11px] font-medium text-strong" title={pending.path}>
            {pending.fileName}
          </span>
        </div>
        <span className="rounded bg-fill-hover px-1 py-0.2 text-[9px] text-faint">文件</span>
        {!sending && (
          <button
            aria-label="移除附加文件"
            className="shrink-0 rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
            onClick={onRemove}
            type="button"
          >
            <X className="size-3" />
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
      <FolderOpen className="size-3.5 shrink-0 text-amber-400" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[11px] font-medium text-strong" title={pending.path}>
          {pending.folderName}
        </span>
      </div>
      <span className="rounded bg-fill-hover px-1 py-0.2 text-[9px] text-faint">文件夹</span>
      {!sending && (
        <button
          aria-label="移除附加文件夹"
          className="shrink-0 rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
          onClick={onRemove}
          type="button"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}
