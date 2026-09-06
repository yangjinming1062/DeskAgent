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
} from '@/chat/chat-store'
import { Brain, Loader2, Sparkles, Thermometer } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'
import type { SessionMessage } from '@/shared/types/spiritagent'

const DEFAULT_THRESHOLD = 0.8
const DEFAULT_LIMIT = 1_000_000
const DEFAULT_TEMPERATURE = 0.7

export const REASONING_OPTIONS = [
  { label: '关闭', value: 'none' },
  { label: '低', value: 'low' },
  { label: '中', value: 'medium' },
  { label: '高', value: 'high' }
] as const

export type ReasoningEffort = (typeof REASONING_OPTIONS)[number]['value']

export interface CompressContextResponse {
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

export function formatTokenNumber(num: number): string {
  if (num >= 1_000_000) {
    return `${(num / 1_000_000).toFixed(1)}M`
  }

  if (num >= 1_000) {
    return `${(num / 1_000).toFixed(1)}k`
  }

  return num.toLocaleString()
}

function isReasoningEffort(value: string): value is ReasoningEffort {
  return REASONING_OPTIONS.some(opt => opt.value === value)
}

export function resolveReasoningEffort(value: unknown): ReasoningEffort {
  return typeof value === 'string' && isReasoningEffort(value) ? value : 'none'
}

export function resolveTemperature(value: unknown): number {
  return typeof value === 'number' ? value : DEFAULT_TEMPERATURE
}

export function temperatureStyleLabel(temp: number): string {
  return temp <= 0.35 ? '严谨' : temp <= 0.75 ? '平衡' : '发散'
}

function reasoningOptionLabel(value: ReasoningEffort): string {
  return REASONING_OPTIONS.find(opt => opt.value === value)?.label ?? '关闭'
}

export function useContextStatus(): {
  barColor: string
  contextLimit: number
  isHealthy: boolean
  isInactive: boolean
  isWarning: boolean
  pct: number
  sessionId: string | null
  threshold: number
  thresholdPct: number
  totalTokens: number
} {
  const sessionId = useStore($chatSessionId)
  const usage = useStore($sessionContextUsage)
  const settings = useStore($sessionSettings)

  const threshold =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const totalTokens = usage.totalTokens
  const contextLimit = usage.contextLimit > 0 ? usage.contextLimit : DEFAULT_LIMIT
  const rawPct = (totalTokens / contextLimit) * 100
  const pct = clamp(rawPct, 0, 100)
  const thresholdPct = clamp(threshold * 100, 1, 100)

  const isInactive = totalTokens <= 0 || pct < 2
  const isHealthy = !isInactive && pct < thresholdPct * 0.5
  const isWarning = !isInactive && !isHealthy && pct < thresholdPct * 0.88

  const barColor = isInactive
    ? 'bg-fill-faint'
    : isHealthy
      ? 'bg-emerald-400'
      : isWarning
        ? 'bg-amber-400'
        : 'bg-rose-500'

  return {
    barColor,
    contextLimit,
    isHealthy,
    isInactive,
    isWarning,
    pct,
    sessionId,
    threshold,
    thresholdPct,
    totalTokens
  }
}

/** 顶栏极简环境感知细线：1.5px 极细微进度条，带阈值标记刻度 */
export function ChatContextAmbientLine(): React.JSX.Element {
  const { pct, thresholdPct, isInactive, isHealthy, isWarning } = useContextStatus()

  const lineColor = isInactive
    ? 'bg-fill-faint'
    : isHealthy
      ? 'bg-gradient-to-r from-emerald-500 to-emerald-400'
      : isWarning
        ? 'bg-gradient-to-r from-amber-500 to-amber-400'
        : 'bg-gradient-to-r from-rose-500 to-rose-400'

  return (
    <div className="relative h-[1.5px] w-full bg-line-hairline overflow-hidden select-none">
      <div
        className={cn('h-full transition-all duration-300 ease-out', lineColor)}
        style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
      />
      <div
        className="absolute top-0 bottom-0 w-[1px] bg-line-strong z-10 opacity-70"
        style={{ left: `${thresholdPct}%` }}
        title={`自动压缩阈值节点 (${Math.round(thresholdPct)}%)`}
      />
    </div>
  )
}

export interface ChatCapsuleProps {
  active?: boolean
  onClick?: () => void
  variant?: 'workbench' | 'living'
}

function capsuleClassName(active: boolean | undefined, variant: ChatCapsuleProps['variant']): string {
  return cn(
    'inline-flex h-6 items-center gap-1.5 rounded-full border border-line-hairline bg-fill-faint px-2 text-[10px] text-body transition hover:border-line-standard hover:bg-fill-hover hover:text-strong cursor-pointer select-none',
    active && 'border-accent/40 bg-accent/15 text-accent shadow-xs',
    variant === 'living' && 'h-5 px-1.5 text-[9.5px]'
  )
}

/** 顶栏上下文胶囊微徽标：展示健康指示点、实时 Token 数及百分比 */
export function ChatContextCapsule({ active, onClick, variant }: ChatCapsuleProps): React.JSX.Element {
  const { totalTokens, contextLimit, pct, isInactive, isHealthy, isWarning, thresholdPct } = useContextStatus()

  const dotColor = isInactive
    ? 'bg-muted-foreground/40'
    : isHealthy
      ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]'
      : isWarning
        ? 'bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]'
        : 'bg-rose-500 shadow-[0_0_6px_rgba(244,63,94,0.7)]'

  const titleText = `当前会话上下文：${totalTokens.toLocaleString()} / ${contextLimit.toLocaleString()} Tokens (${pct.toFixed(1)}%) · 自动压缩阈值: ${Math.round(thresholdPct)}% · 点击展开管理`

  return (
    <button
      aria-label="查看上下文记忆与压缩管理"
      className={capsuleClassName(active, variant)}
      onClick={onClick}
      title={titleText}
      type="button"
    >
      <span className={cn('size-1.5 rounded-full shrink-0 transition-colors', dotColor)} />
      <span className="font-mono text-[10px] tracking-tight">
        {formatTokenNumber(totalTokens)}
        <span className="text-faint font-sans"> / </span>
        <span className="text-muted">{formatTokenNumber(contextLimit)}</span>
      </span>
      <span className="text-[9px] text-faint">({pct.toFixed(0)}%)</span>
    </button>
  )
}

export function ChatTemperatureCapsule({ active, onClick, variant }: ChatCapsuleProps): React.JSX.Element {
  const settings = useStore($sessionSettings)
  const temp = resolveTemperature(settings.temperature)
  const tempLabel = temperatureStyleLabel(temp)
  const titleText = `当前会话采样温度: ${temp.toFixed(2)} (${tempLabel}) · 点击配置`

  return (
    <button
      aria-label="配置当前会话采样温度"
      className={capsuleClassName(active, variant)}
      onClick={onClick}
      title={titleText}
      type="button"
    >
      <Thermometer className="size-3 text-accent shrink-0" />
      <span className="font-mono text-[10px] tracking-tight">{temp.toFixed(2)}</span>
      {variant !== 'living' && <span className="text-[9px] text-faint">({tempLabel})</span>}
    </button>
  )
}

export function ChatReasoningCapsule({ active, onClick, variant }: ChatCapsuleProps): React.JSX.Element {
  const settings = useStore($sessionSettings)
  const reasoning = resolveReasoningEffort(settings.reasoning_effort)
  const label = reasoningOptionLabel(reasoning)
  const isOff = reasoning === 'none'
  const titleText = `当前会话思考推理深度: ${isOff ? '已关闭' : label} · 点击配置`

  return (
    <button
      aria-label="配置当前会话思考推理深度"
      className={capsuleClassName(active, variant)}
      onClick={onClick}
      title={titleText}
      type="button"
    >
      <Brain className={cn('size-3 shrink-0', isOff ? 'text-faint' : 'text-accent')} />
      <span className="text-[10px] tracking-tight">{isOff ? '思考关' : `思考: ${label}`}</span>
    </button>
  )
}

/** 兼容保留的完整进度条与手动压缩组件 */
export function ContextProgressBar(): React.JSX.Element {
  const { sessionId, totalTokens, contextLimit, pct, thresholdPct, isInactive, barColor } = useContextStatus()
  const gateway = useStore($gateway)
  const [hovered, setHovered] = useState(false)
  const [compressing, setCompressing] = useState(false)

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
        <div
          className={cn('h-full rounded-full transition-all duration-300 ease-out', barColor)}
          style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
        />
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
