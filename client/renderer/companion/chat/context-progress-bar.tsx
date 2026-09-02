import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import type React from 'react'
import { useState } from 'react'

import {
  $chatSessionId,
  $sessionContextUsage,
  $sessionSettings,
  hydrateChatMessages,
  setSessionContextUsage
} from '@/companion/chat-store'
import { Loader2, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'
import type { SessionMessage } from '@/shared/types/spiritagent'

const DEFAULT_THRESHOLD = 0.8
const DEFAULT_LIMIT = 1_000_000

interface CompressContextResponse {
  compressed: boolean
  messages?: SessionMessage[]
  reason?: string
  replaced_count?: number
  session_id?: string
  summary?: string
  usage?: {
    context_window?: number
    total_tokens?: number
  }
}

export function ContextProgressBar(): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const usage = useStore($sessionContextUsage)
  const settings = useStore($sessionSettings)
  const gateway = useStore($gateway)
  const [hovered, setHovered] = useState(false)
  const [compressing, setCompressing] = useState(false)

  const threshold =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const totalTokens = usage.totalTokens
  const contextLimit = usage.contextLimit > 0 ? usage.contextLimit : DEFAULT_LIMIT
  const rawPct = (totalTokens / contextLimit) * 100
  const pct = clamp(rawPct, 0, 100)
  const thresholdPct = clamp(threshold * 100, 1, 100)

  // 色彩逻辑：
  // 1. 刚开始/基本无占用（< 2%）：未激活的灰色
  // 2. 健康占用（< 阈值的 50%，如 < 40%）：健康的绿色
  // 3. 上下文逐渐变长（40% ~ 阈值的 88%，如 40% ~ 70%）：黄色
  // 4. 靠近或超过压缩节点（>= 阈值的 88%）：红色
  const isInactive = totalTokens <= 0 || pct < 2
  const isHealthy = !isInactive && pct < thresholdPct * 0.5
  const isWarning = !isInactive && !isHealthy && pct < thresholdPct * 0.88

  const barColor = isInactive
    ? 'bg-fill-faint'
    : isHealthy
      ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.35)]'
      : isWarning
        ? 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.35)]'
        : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.45)]'

  const handleManualCompress = async (e: React.MouseEvent) => {
    e.stopPropagation()

    if (compressing || !sessionId || !gateway || gateway.connectionState !== 'open') {
      return
    }

    setCompressing(true)

    try {
      const res = await gateway.request<CompressContextResponse>('session.compress_context', {
        session_id: sessionId
      })

      if (res.compressed) {
        if (Array.isArray(res.messages)) {
          hydrateChatMessages(res.messages)
        }

        if (res.usage?.total_tokens !== undefined) {
          setSessionContextUsage({
            contextLimit: res.usage.context_window,
            totalTokens: res.usage.total_tokens
          })
        }

        notify({
          durationMs: 4000,
          kind: 'success',
          message: `已成功压缩 ${res.replaced_count ?? 0} 条早期对话历史`
        })
      } else {
        notify({
          durationMs: 3500,
          kind: 'info',
          message: res.reason || '当前历史消息较少，无需压缩'
        })
      }
    } catch (err) {
      notifyError(err, '手动压缩上下文失败')
    } finally {
      setCompressing(false)
    }
  }

  return (
    <div
      className="relative w-full pt-1.5 pb-0.5"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* 悬停浮层提示信息 */}
      {hovered && (
        <div className="absolute -top-8 left-1/2 -translate-x-1/2 z-50 flex items-center gap-1.5 rounded-md border border-line-standard bg-neutral-900/95 px-2.5 py-1 text-[10px] text-strong shadow-lg backdrop-blur-sm whitespace-nowrap pointer-events-none animate-in fade-in zoom-in-95 duration-150">
          {compressing ? (
            <div className="flex items-center gap-1.5 text-accent">
              <Loader2 className="size-3 animate-spin" />
              <span>正在压缩当前会话上下文…</span>
            </div>
          ) : (
            <>
              <span>
                上下文：<strong className="font-mono text-strong">{totalTokens.toLocaleString()}</strong> /{' '}
                <span className="font-mono text-muted">{contextLimit.toLocaleString()}</span> Tokens ({pct.toFixed(1)}%)
              </span>
              <span className="text-faint">·</span>
              <span className="text-accent font-medium">压缩节点: {Math.round(thresholdPct)}%</span>
              <span className="text-faint">·</span>
              <span className="text-muted font-sans flex items-center gap-0.5">
                <Sparkles className="size-2.5 text-amber-300" />
                点击立即压缩
              </span>
            </>
          )}
        </div>
      )}

      {/* 进度条轨道（可点击触发压缩） */}
      <button
        aria-label="手动压缩上下文"
        className={cn(
          'relative h-1.5 w-full overflow-visible rounded-full bg-fill-hover transition group cursor-pointer block border-0 p-0',
          compressing && 'cursor-wait animate-pulse'
        )}
        disabled={compressing}
        onClick={handleManualCompress}
        title="点击手动压缩当前会话上下文"
        type="button"
      >
        {/* 填充条 */}
        <div
          className={cn('h-full rounded-full transition-all duration-300 ease-out', barColor)}
          style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
        />

        {/* 压缩阈值节点标识 (Node Marker) */}
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-2.5 w-[2px] rounded-full bg-line-strong shadow-xs transition group-hover:h-3.5"
          style={{ left: `${thresholdPct}%` }}
          title={`压缩阈值节点 (${Math.round(thresholdPct)}%)`}
        >
          <div className="absolute -top-1 left-1/2 -translate-x-1/2 size-1 rounded-full bg-line-strong" />
        </div>
      </button>
    </div>
  )
}
