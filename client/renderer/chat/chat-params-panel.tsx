import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  $chatSessionId,
  $sessionSettings,
  hydrateChatMessages,
  setSessionContextUsage,
  updateSessionSetting
} from '@/chat/chat-store'
import { Brain, type IconComponent, Loader2, RefreshCw, Sparkles, Thermometer, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'

import {
  type CompressContextResponse,
  formatTokenNumber,
  REASONING_OPTIONS,
  type ReasoningEffort,
  resolveReasoningEffort,
  resolveTemperature,
  temperatureStyleLabel,
  useContextStatus
} from './context-progress-bar'

const DEFAULT_TEMPERATURE = 0.7
const DEFAULT_THRESHOLD = 0.8
const DEFAULT_REASONING: ReasoningEffort = 'none'
const THRESHOLD_MIN = 0.3
const THRESHOLD_STEP = 0.05

export type ChatParamsTab = 'context' | 'temperature' | 'reasoning'

const PARAM_TABS: { icon: IconComponent; id: ChatParamsTab; label: string }[] = [
  { icon: Sparkles, id: 'context', label: '上下文' },
  { icon: Thermometer, id: 'temperature', label: '温度' },
  { icon: Brain, id: 'reasoning', label: '思考' }
]

const TEMPERATURE_PRESETS = [
  { label: '严谨', value: 0.2 },
  { label: '平衡', value: 0.7 },
  { label: '发散', value: 1 }
] as const

const REASONING_HINTS: Record<ReasoningEffort, string> = {
  high: '深推理，适合复杂方案与证明。',
  low: '短思考，适合简单问答。',
  medium: '常规推导，适合开发与排错。',
  none: '直接作答，响应最快。'
}

interface DraggableThresholdBarProps {
  barColor: string
  disabled?: boolean
  isInactive?: boolean
  onChange: (threshold: number) => void
  pct: number
  thresholdPct: number
}

function snapThreshold(value: number): number {
  return clamp(Math.round(value / THRESHOLD_STEP) * THRESHOLD_STEP, THRESHOLD_MIN, 1)
}

function DraggableThresholdBar({
  barColor,
  disabled,
  isInactive,
  onChange,
  pct,
  thresholdPct
}: DraggableThresholdBarProps): React.JSX.Element {
  const trackRef = useRef<HTMLDivElement>(null)
  const draggingRef = useRef(false)
  const [isDragging, setIsDragging] = useState(false)
  const [isHoveringThumb, setIsHoveringThumb] = useState(false)

  const calcRatioFromClientX = useCallback((clientX: number): number | null => {
    if (!trackRef.current) {
      return null
    }

    const rect = trackRef.current.getBoundingClientRect()

    if (rect.width <= 0) {
      return null
    }

    return snapThreshold((clientX - rect.left) / rect.width)
  }, [])

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (disabled) {
      return
    }

    e.preventDefault()
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)
    draggingRef.current = true
    setIsDragging(true)

    const ratio = calcRatioFromClientX(e.clientX)

    if (ratio !== null) {
      onChange(ratio)
    }
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (!draggingRef.current) {
      return
    }

    e.preventDefault()
    const ratio = calcRatioFromClientX(e.clientX)

    if (ratio !== null) {
      onChange(ratio)
    }
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }

    if (!draggingRef.current) {
      return
    }

    draggingRef.current = false
    setIsDragging(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (disabled) {
      return
    }

    const current = thresholdPct / 100

    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault()
      onChange(snapThreshold(current - THRESHOLD_STEP))
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault()
      onChange(snapThreshold(current + THRESHOLD_STEP))
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-1.5 font-medium text-body">
          <span>上下文窗口负载</span>
          <span className="text-[10px] text-faint">/ 自动压缩线</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-accent font-medium">触发节点:</span>
          <span className="font-mono text-xs font-semibold text-strong">{Math.round(thresholdPct)}%</span>
        </div>
      </div>

      <div
        className={cn(
          'relative h-5 w-full cursor-pointer flex items-center select-none group touch-none py-1',
          isDragging && 'cursor-grabbing'
        )}
        onPointerCancel={handlePointerUp}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        ref={trackRef}
      >
        <div className="relative h-2 w-full rounded-full bg-fill-hover overflow-hidden pointer-events-none">
          <div
            className={cn('h-full rounded-full transition-all duration-300 ease-out', barColor)}
            style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
          />
        </div>

        <div
          aria-label="自动压缩阈值触发线（左右拖动调节）"
          aria-valuemax={100}
          aria-valuemin={30}
          aria-valuenow={Math.round(thresholdPct)}
          className={cn(
            'absolute top-1/2 -translate-y-1/2 -translate-x-1/2 z-20 flex flex-col items-center cursor-ew-resize',
            isDragging && 'cursor-grabbing'
          )}
          onKeyDown={handleKeyDown}
          onMouseEnter={() => setIsHoveringThumb(true)}
          onMouseLeave={() => setIsHoveringThumb(false)}
          role="slider"
          style={{ left: `${thresholdPct}%` }}
          tabIndex={0}
        >
          <div
            className={cn(
              'absolute -top-7 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium shadow-md transition-all pointer-events-none whitespace-nowrap border border-accent/40',
              isDragging || isHoveringThumb
                ? 'opacity-100 scale-100 -translate-y-0.5 bg-neutral-900 text-accent ring-1 ring-accent/30'
                : 'opacity-0 scale-90 translate-y-1'
            )}
          >
            {Math.round(thresholdPct)}%
          </div>

          <div
            className={cn(
              'w-2 h-4 rounded-full border border-line-strong bg-accent shadow-sm flex items-center justify-center transition-all duration-150',
              (isDragging || isHoveringThumb) && 'h-5 scale-110 ring-2 ring-accent/40 bg-accent-hover'
            )}
          >
            <div className="h-2.5 w-[1px] bg-white/80 rounded-full" />
          </div>
        </div>
      </div>

      <div className="flex justify-between items-center text-[9px] text-faint select-none">
        <span>30% 紧凑压缩</span>
        <span className="text-[10px] text-accent/85 font-medium">左右拖动手柄调节阈值</span>
        <span>100% 满额触发</span>
      </div>

      <p className="text-[10px] text-muted leading-relaxed">
        当会话上下文达到设定比例时，后台自动总结提炼早期历史，释放空间保障记忆连贯。
      </p>
    </div>
  )
}

interface ChatParamsPanelProps {
  activeTab: ChatParamsTab
  onClose?: () => void
  onTabChange: (tab: ChatParamsTab) => void
}

export function ChatParamsPanel({ activeTab, onClose, onTabChange }: ChatParamsPanelProps): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const settings = useStore($sessionSettings)
  const gateway = useStore($gateway)
  const contextStatus = useContextStatus()
  const [compressing, setCompressing] = useState(false)

  const tempValue = resolveTemperature(settings.temperature)

  const thresholdValue =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const reasoningValue = resolveReasoningEffort(settings.reasoning_effort)

  const [temp, setTemp] = useState(tempValue)
  const [threshold, setThreshold] = useState(thresholdValue)
  const [reasoning, setReasoning] = useState<ReasoningEffort>(reasoningValue)

  useEffect(() => {
    setTemp(tempValue)
  }, [tempValue])

  useEffect(() => {
    setThreshold(thresholdValue)
  }, [thresholdValue])

  useEffect(() => {
    setReasoning(reasoningValue)
  }, [reasoningValue])

  type SessionSettingsPatch = {
    context_compression_threshold?: number
    reasoning_effort?: ReasoningEffort
    temperature?: number
  }

  const pendingPatchRef = useRef<SessionSettingsPatch>({})
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const targetSessionIdRef = useRef(sessionId)
  targetSessionIdRef.current = sessionId
  const prevSessionIdRef = useRef(sessionId)

  const flushPending = useCallback((): void => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }

    const patch = pendingPatchRef.current
    const targetId = targetSessionIdRef.current

    if (Object.keys(patch).length === 0 || !targetId || !gateway || gateway.connectionState !== 'open') {
      return
    }

    pendingPatchRef.current = {}
    void gateway
      .request('session.set_settings', {
        session_id: targetId,
        settings: patch
      })
      .catch(() => {
        /* 尽力而为 */
      })
  }, [gateway])

  const scheduleSync = useCallback(
    (incremental: SessionSettingsPatch): void => {
      Object.assign(pendingPatchRef.current, incremental)

      if (!sessionId || !gateway || gateway.connectionState !== 'open') {
        return
      }

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        flushPending()
      }, 350)
    },
    [sessionId, gateway, flushPending]
  )

  useEffect(() => {
    if (prevSessionIdRef.current && prevSessionIdRef.current !== sessionId) {
      flushPending()
    }

    prevSessionIdRef.current = sessionId
    targetSessionIdRef.current = sessionId
  }, [sessionId, flushPending])

  useEffect(
    () => () => {
      flushPending()
    },
    [flushPending]
  )

  const handleTempChange = (val: number): void => {
    const rounded = Math.round(val * 100) / 100
    setTemp(rounded)
    updateSessionSetting('temperature', rounded)
    scheduleSync({ temperature: rounded })
  }

  const handleThresholdChange = (val: number): void => {
    const rounded = snapThreshold(val)
    setThreshold(rounded)
    updateSessionSetting('context_compression_threshold', rounded)
    scheduleSync({ context_compression_threshold: rounded })
  }

  const handleReasoningChange = (val: ReasoningEffort): void => {
    setReasoning(val)
    updateSessionSetting('reasoning_effort', val)
    scheduleSync({ reasoning_effort: val })
  }

  const handleResetDefaults = (): void => {
    handleTempChange(DEFAULT_TEMPERATURE)
    handleThresholdChange(DEFAULT_THRESHOLD)
    handleReasoningChange(DEFAULT_REASONING)
    notify({ durationMs: 2500, kind: 'info', message: '已恢复当前会话参数为默认配置' })
  }

  const handleManualCompress = async (): Promise<void> => {
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
          message: `已成功整理并压缩 ${res.replaced_count ?? 0} 条历史会话`
        })
      } else {
        notify({
          durationMs: 3500,
          kind: 'info',
          message: res.reason || '当前历史消息较少，暂无需压缩'
        })
      }
    } catch (err) {
      notifyError(err, '手动压缩上下文失败')
    } finally {
      setCompressing(false)
    }
  }

  const tempPct = Math.round(temp * 100)
  const thresholdPct = Math.round(threshold * 100)
  const tempSemantics = temperatureStyleLabel(temp)

  return (
    <div className="w-[350px] flex flex-col gap-3 rounded-2xl border border-line-standard bg-surface-card/95 p-3.5 text-left shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150 select-none">
      <div className="flex items-center justify-between border-b border-line-hairline pb-2.5">
        <div className="flex items-center gap-1 bg-fill-faint p-0.5 rounded-lg border border-line-hairline">
          {PARAM_TABS.map(tab => {
            const Icon = tab.icon
            const active = activeTab === tab.id

            return (
              <button
                className={cn(
                  'flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition cursor-pointer select-none',
                  active ? 'bg-surface-panel text-strong shadow-xs' : 'text-muted hover:text-strong hover:bg-fill-hover'
                )}
                key={tab.id}
                onClick={() => onTabChange(tab.id)}
                type="button"
              >
                <Icon className="size-3.5 text-accent" />
                <span>{tab.label}</span>
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-1.5">
          <span
            className="text-[10px] font-medium text-accent bg-accent/10 border border-accent/20 px-1.5 py-0.5 rounded-md select-none cursor-default"
            title="此面板修改仅保存在当前会话中，独立生效，不会改变全局默认配置"
          >
            当前会话
          </span>
          {onClose && (
            <button
              aria-label="关闭面板"
              className="flex size-6 items-center justify-center rounded-lg text-faint hover:bg-fill-hover hover:text-strong transition cursor-pointer"
              onClick={onClose}
              type="button"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      {activeTab === 'context' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          <div className="grid grid-cols-3 gap-2">
            <div className="flex flex-col gap-0.5 rounded-xl border border-line-hairline bg-fill-faint p-2">
              <span className="text-[10px] text-muted">当前已用</span>
              <span className="font-mono text-xs font-semibold text-strong truncate">
                {contextStatus.totalTokens.toLocaleString()}
              </span>
              <span className="text-[9px] text-faint">Tokens</span>
            </div>

            <div className="flex flex-col gap-0.5 rounded-xl border border-line-hairline bg-fill-faint p-2">
              <span className="text-[10px] text-muted">最大容量</span>
              <span className="font-mono text-xs font-semibold text-strong truncate">
                {formatTokenNumber(contextStatus.contextLimit)}
              </span>
              <span className="text-[9px] text-faint">Tokens</span>
            </div>

            <div className="flex flex-col gap-0.5 rounded-xl border border-line-hairline bg-fill-faint p-2">
              <span className="text-[10px] text-muted">占用比例</span>
              <span className="font-mono text-xs font-semibold text-strong">{contextStatus.pct.toFixed(1)}%</span>
              <span className="text-[9px] text-faint">节点 {Math.round(thresholdPct)}%</span>
            </div>
          </div>

          <DraggableThresholdBar
            barColor={contextStatus.barColor}
            isInactive={contextStatus.isInactive}
            onChange={handleThresholdChange}
            pct={contextStatus.pct}
            thresholdPct={thresholdPct}
          />

          <div
            className={cn(
              'flex items-center gap-2 rounded-xl p-2.5 text-xs leading-relaxed border',
              contextStatus.isInactive || contextStatus.isHealthy
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
                : contextStatus.isWarning
                  ? 'bg-amber-500/10 border-amber-500/20 text-amber-300'
                  : 'bg-rose-500/10 border-rose-500/20 text-rose-300'
            )}
          >
            <Sparkles className="size-4 shrink-0" />
            <span className="text-[11px]">
              {contextStatus.isInactive || contextStatus.isHealthy
                ? '上下文状态充裕，拥有充沛的记忆空间保障顺畅交流。'
                : contextStatus.isWarning
                  ? '上下文逐渐累积，靠近自动压缩阈值，可随时整理。'
                  : '上下文负荷较高，已达到自动压缩线，建议立即整理记忆。'}
            </span>
          </div>

          <button
            className={cn(
              'flex items-center justify-center gap-2 w-full rounded-xl py-2 px-3 text-xs font-medium transition cursor-pointer border',
              compressing
                ? 'bg-fill-hover text-muted cursor-wait border-line-standard'
                : 'bg-accent/15 hover:bg-accent/25 text-accent border-accent/30 hover:border-accent/50 shadow-xs'
            )}
            disabled={compressing || contextStatus.totalTokens <= 0}
            onClick={() => {
              void handleManualCompress()
            }}
            type="button"
          >
            {compressing ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                <span>正在提取提炼并压缩记忆…</span>
              </>
            ) : (
              <>
                <Sparkles className="size-3.5 text-accent" />
                <span>整理历史记忆 · 立即压缩上下文</span>
              </>
            )}
          </button>
        </div>
      )}

      {activeTab === 'temperature' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          <div className="flex flex-col gap-2 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 font-medium text-body">
                <Thermometer className="size-3.5 text-accent" />
                <span>采样温度</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-accent font-medium px-1.5 py-0.5 rounded bg-accent/10 border border-accent/20">
                  {tempSemantics}
                </span>
                <span className="font-mono text-xs font-semibold text-strong">{temp.toFixed(2)}</span>
              </div>
            </div>

            <input
              aria-label="采样温度"
              className="sa-slider h-1.5 w-full cursor-pointer accent-accent"
              max={1}
              min={0}
              onChange={e => handleTempChange(Number(e.target.value))}
              step={0.05}
              style={{ '--sa-slider-fill': `${tempPct}%` } as React.CSSProperties}
              type="range"
              value={temp}
            />

            <div className="flex justify-between text-[9px] text-faint select-none">
              <span>0.00 严谨</span>
              <span>0.70 平衡</span>
              <span>1.00 发散</span>
            </div>

            <div className="grid grid-cols-3 gap-1.5 pt-1">
              {TEMPERATURE_PRESETS.map(preset => {
                const active = Math.abs(temp - preset.value) < 0.05

                return (
                  <button
                    className={cn(
                      'flex flex-col items-center py-1.5 px-1 rounded-lg border text-center transition cursor-pointer select-none',
                      active
                        ? 'border-accent bg-accent/15 text-accent shadow-xs'
                        : 'border-line-hairline bg-surface-panel/40 text-muted hover:bg-fill-hover hover:text-strong'
                    )}
                    key={preset.value}
                    onClick={() => handleTempChange(preset.value)}
                    type="button"
                  >
                    <span className="font-mono text-[11px] font-semibold">{preset.value.toFixed(2)}</span>
                    <span className="text-[9px]">{preset.label}</span>
                  </button>
                )
              })}
            </div>
          </div>

          <p className="text-[10px] text-faint px-0.5">低更确定，高更发散。仅对当前会话生效。</p>
        </div>
      )}

      {activeTab === 'reasoning' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          <div className="flex flex-col gap-2 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 font-medium text-body">
                <Brain className="size-3.5 text-accent" />
                <span>思考推理深度</span>
              </div>
              <span className="font-semibold text-xs text-accent px-1.5 py-0.5 rounded bg-accent/10 border border-accent/20">
                {REASONING_OPTIONS.find(opt => opt.value === reasoning)?.label ?? '关闭'}
              </span>
            </div>

            <div className="grid grid-cols-4 gap-1 rounded-lg border border-line-hairline bg-fill-hover/50 p-1">
              {REASONING_OPTIONS.map(opt => {
                const active = reasoning === opt.value

                return (
                  <button
                    className={cn(
                      'rounded-md py-1.5 text-xs font-medium transition cursor-pointer text-center select-none',
                      active ? 'bg-accent text-on-accent shadow-xs' : 'text-muted hover:bg-fill-hover hover:text-strong'
                    )}
                    key={opt.value}
                    onClick={() => handleReasoningChange(opt.value)}
                    type="button"
                  >
                    {opt.label}
                  </button>
                )
              })}
            </div>
          </div>

          <p className="text-[10px] text-muted leading-relaxed px-0.5">{REASONING_HINTS[reasoning]}</p>
        </div>
      )}

      <div className="flex items-center justify-between border-t border-line-hairline pt-2 text-[10px]">
        <span
          className="text-faint flex items-center gap-1.5 select-none"
          title="此配置仅对当前会话生效，自动持久化保存"
        >
          <span className="size-1.5 rounded-full bg-accent" />
          <span>仅对当前会话生效 · 自动保存</span>
        </span>
        <button
          className="flex items-center gap-1 text-[11px] text-muted hover:text-strong transition cursor-pointer"
          onClick={handleResetDefaults}
          title="将当前会话的对话参数重置为默认配置"
          type="button"
        >
          <RefreshCw className="size-3" />
          <span>恢复默认</span>
        </button>
      </div>
    </div>
  )
}
