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
import { Brain, Loader2, RefreshCw, SlidersHorizontal, Sparkles, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'

import { type CompressContextResponse, formatTokenNumber, useContextStatus } from './context-progress-bar'

const DEFAULT_TEMPERATURE = 0.7
const DEFAULT_THRESHOLD = 0.8
const DEFAULT_REASONING = 'none'

const REASONING_OPTIONS = [
  { label: '关闭', value: 'none' },
  { label: '低', value: 'low' },
  { label: '中', value: 'medium' },
  { label: '高', value: 'high' }
] as const

interface ChatParamsPanelProps {
  defaultTab?: 'context' | 'params'
  onClose?: () => void
}

export function ChatParamsPanel({ defaultTab = 'params', onClose }: ChatParamsPanelProps): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<'context' | 'params'>(defaultTab)
  const sessionId = useStore($chatSessionId)
  const settings = useStore($sessionSettings)
  const gateway = useStore($gateway)
  const contextStatus = useContextStatus()
  const [compressing, setCompressing] = useState(false)

  // 读取当前会话的温度、压缩阈值与推理等级
  const tempValue = typeof settings.temperature === 'number' ? settings.temperature : DEFAULT_TEMPERATURE

  const thresholdValue =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const reasoningValue = typeof settings.reasoning_effort === 'string' ? settings.reasoning_effort : DEFAULT_REASONING

  const [temp, setTemp] = useState(tempValue)
  const [threshold, setThreshold] = useState(thresholdValue)
  const [reasoning, setReasoning] = useState(reasoningValue)

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
    reasoning_effort?: string
    temperature?: number
  }

  const pendingPatchRef = useRef<SessionSettingsPatch>({})
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const targetSessionIdRef = useRef(sessionId)
  targetSessionIdRef.current = sessionId
  const prevSessionIdRef = useRef(sessionId)

  const flushPending = useCallback(() => {
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
    (incremental: SessionSettingsPatch) => {
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

  const handleTempChange = (val: number) => {
    const rounded = Math.round(val * 100) / 100
    setTemp(rounded)
    updateSessionSetting('temperature', rounded)
    scheduleSync({ temperature: rounded })
  }

  const handleThresholdChange = (val: number) => {
    const rounded = clamp(Math.round(val * 100) / 100, 0.3, 1.0)
    setThreshold(rounded)
    updateSessionSetting('context_compression_threshold', rounded)
    scheduleSync({ context_compression_threshold: rounded })
  }

  const handleReasoningChange = (val: string) => {
    setReasoning(val)
    updateSessionSetting('reasoning_effort', val)
    scheduleSync({ reasoning_effort: val })
  }

  const handleResetDefaults = () => {
    handleTempChange(DEFAULT_TEMPERATURE)
    handleThresholdChange(DEFAULT_THRESHOLD)
    handleReasoningChange(DEFAULT_REASONING)
    notify({ durationMs: 2500, kind: 'info', message: '已恢复当前会话默认参数' })
  }

  const handleManualCompress = async () => {
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

  const tempSemantics = temp <= 0.35 ? '严谨精准' : temp <= 0.75 ? '平衡稳健' : '富有创意'

  return (
    <div className="w-[340px] flex flex-col gap-3 rounded-2xl border border-line-standard bg-surface-card/95 p-3.5 text-left shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150 select-none">
      {/* 顶部标签切换 & 关闭 */}
      <div className="flex items-center justify-between border-b border-line-hairline pb-2.5">
        <div className="flex items-center gap-1 bg-fill-faint p-0.5 rounded-lg border border-line-hairline">
          <button
            className={cn(
              'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition cursor-pointer',
              activeTab === 'context'
                ? 'bg-surface-panel text-strong shadow-xs'
                : 'text-muted hover:text-strong hover:bg-fill-hover'
            )}
            onClick={() => setActiveTab('context')}
            type="button"
          >
            <Brain className="size-3.5 text-accent" />
            <span>上下文记忆</span>
          </button>
          <button
            className={cn(
              'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition cursor-pointer',
              activeTab === 'params'
                ? 'bg-surface-panel text-strong shadow-xs'
                : 'text-muted hover:text-strong hover:bg-fill-hover'
            )}
            onClick={() => setActiveTab('params')}
            type="button"
          >
            <SlidersHorizontal className="size-3.5 text-accent" />
            <span>对话参数</span>
          </button>
        </div>

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

      {/* Tab 1: 上下文记忆与占用 */}
      {activeTab === 'context' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          {/* 三格指标卡 */}
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
              <span className="text-[9px] text-faint">节点 {Math.round(contextStatus.thresholdPct)}%</span>
            </div>
          </div>

          {/* 进度条与阈值刻度 */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-[10px]">
              <span className="text-muted">上下文窗口负载</span>
              <span className="text-accent font-medium">阈值触发线: {Math.round(contextStatus.thresholdPct)}%</span>
            </div>

            <div className="relative h-2 w-full rounded-full bg-fill-hover overflow-visible">
              <div
                className={cn('h-full rounded-full transition-all duration-300 ease-out', contextStatus.barColor)}
                style={{ width: `${Math.max(contextStatus.pct, contextStatus.isInactive ? 0 : 1)}%` }}
              />
              <div
                className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-3.5 w-[2px] rounded-full bg-line-strong shadow-xs"
                style={{ left: `${contextStatus.thresholdPct}%` }}
                title={`压缩阈值节点 (${Math.round(contextStatus.thresholdPct)}%)`}
              />
            </div>
          </div>

          {/* 状态徽标提示 */}
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

          {/* 立即压缩按钮 */}
          <button
            className={cn(
              'flex items-center justify-center gap-2 w-full rounded-xl py-2 px-3 text-xs font-medium transition cursor-pointer border',
              compressing
                ? 'bg-fill-hover text-muted cursor-wait border-line-standard'
                : 'bg-accent/15 hover:bg-accent/25 text-accent border-accent/30 hover:border-accent/50 shadow-xs'
            )}
            disabled={compressing || contextStatus.totalTokens <= 0}
            onClick={handleManualCompress}
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

      {/* Tab 2: 对话生成与推理参数 */}
      {activeTab === 'params' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          {/* 采样温度 */}
          <div className="flex flex-col gap-1.5 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-body font-medium">采样温度</span>
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-accent font-medium">{tempSemantics}</span>
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
            <div className="flex justify-between text-[9px] text-faint">
              <span>0.0 严谨求实</span>
              <span>0.7 平衡推荐</span>
              <span>1.0 发散创意</span>
            </div>
          </div>

          {/* 压缩阈值 */}
          <div className="flex flex-col gap-1.5 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-body font-medium">自动压缩阈值</span>
              <span className="font-mono text-xs font-semibold text-strong">{thresholdPct}%</span>
            </div>
            <input
              aria-label="压缩阈值"
              className="sa-slider h-1.5 w-full cursor-pointer accent-accent"
              max={1.0}
              min={0.3}
              onChange={e => handleThresholdChange(Number(e.target.value))}
              step={0.05}
              style={{ '--sa-slider-fill': `${((threshold - 0.3) / 0.7) * 100}%` } as React.CSSProperties}
              type="range"
              value={threshold}
            />
            <p className="text-[10px] text-muted">当上下文达到该比例时，后台自动总结提炼早期对话历史。</p>
          </div>

          {/* 推理等级 */}
          <div className="flex flex-col gap-1.5 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-body font-medium">思考推理深度</span>
              <span className="text-xs font-semibold text-accent">
                {REASONING_OPTIONS.find(o => o.value === reasoning)?.label ?? '关闭'}
              </span>
            </div>
            <div className="grid grid-cols-4 gap-1 rounded-lg border border-line-hairline bg-fill-hover/50 p-1">
              {REASONING_OPTIONS.map(opt => {
                const active = reasoning === opt.value

                return (
                  <button
                    className={cn(
                      'rounded-md py-1 text-xs font-medium transition cursor-pointer text-center',
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

          {/* 重置参数 */}
          <div className="flex items-center justify-between pt-1">
            <span className="text-[10px] text-faint">修改自动同步至当前话题</span>
            <button
              className="flex items-center gap-1 text-[11px] text-muted hover:text-strong transition cursor-pointer"
              onClick={handleResetDefaults}
              type="button"
            >
              <RefreshCw className="size-3" />
              <span>恢复默认</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
