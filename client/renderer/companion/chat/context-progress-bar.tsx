import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { $sessionContextUsage, $sessionSettings } from '@/companion/chat-store'
import { cn } from '@/shared/lib/utils'

const DEFAULT_THRESHOLD = 0.8
const DEFAULT_LIMIT = 1_000_000

export function ContextProgressBar(): React.JSX.Element {
  const usage = useStore($sessionContextUsage)
  const settings = useStore($sessionSettings)
  const [hovered, setHovered] = useState(false)

  const threshold =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const totalTokens = usage.totalTokens
  const contextLimit = usage.contextLimit > 0 ? usage.contextLimit : DEFAULT_LIMIT
  const rawPct = (totalTokens / contextLimit) * 100
  const pct = Math.min(100, Math.max(0, rawPct))
  const thresholdPct = Math.min(100, Math.max(1, threshold * 100))

  // 色彩逻辑：
  // 1. 刚开始/基本无占用（< 2%）：未激活的灰色
  // 2. 健康占用（< 阈值的 50%，如 < 40%）：健康的绿色
  // 3. 上下文逐渐变长（40% ~ 阈值的 88%，如 40% ~ 70%）：黄色
  // 4. 靠近或超过压缩节点（>= 阈值的 88%）：红色
  const isInactive = totalTokens <= 0 || pct < 2
  const isHealthy = !isInactive && pct < thresholdPct * 0.5
  const isWarning = !isInactive && !isHealthy && pct < thresholdPct * 0.88

  const barColor = isInactive
    ? 'bg-white/20'
    : isHealthy
      ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.35)]'
      : isWarning
        ? 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.35)]'
        : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.45)]'

  return (
    <div
      className="relative w-full pt-1.5 pb-0.5"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* 悬停浮层提示信息 */}
      {hovered && (
        <div className="absolute -top-7 left-1/2 -translate-x-1/2 z-50 flex items-center gap-1.5 rounded-md border border-white/12 bg-neutral-900/95 px-2 py-0.5 text-[10px] text-white/90 shadow-lg backdrop-blur-sm whitespace-nowrap pointer-events-none animate-in fade-in zoom-in-95 duration-150">
          <span>
            上下文：<strong className="font-mono text-white">{totalTokens.toLocaleString()}</strong> /{' '}
            <span className="font-mono text-white/60">{contextLimit.toLocaleString()}</span> Tokens ({pct.toFixed(1)}%)
          </span>
          <span className="text-white/30">·</span>
          <span className="text-accent font-medium">压缩节点: {Math.round(thresholdPct)}%</span>
        </div>
      )}

      {/* 进度条轨道 */}
      <div className="relative h-1 w-full overflow-visible rounded-full bg-white/10 cursor-help transition group">
        {/* 填充条 */}
        <div
          className={cn('h-full rounded-full transition-all duration-300 ease-out', barColor)}
          style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
        />

        {/* 压缩阈值节点标识 (Node Marker) */}
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-2.5 w-[2px] rounded-full bg-white/70 shadow-xs transition hover:bg-white hover:h-3.5"
          style={{ left: `${thresholdPct}%` }}
          title={`压缩阈值节点 (${Math.round(thresholdPct)}%)`}
        >
          <div className="absolute -top-1 left-1/2 -translate-x-1/2 size-1 rounded-full bg-white/80" />
        </div>
      </div>
    </div>
  )
}
