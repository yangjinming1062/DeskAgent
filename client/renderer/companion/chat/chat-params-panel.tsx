import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $chatSessionId, $sessionSettings, updateSessionSetting } from '@/companion/chat-store'
import { SlidersHorizontal } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'

const DEFAULT_TEMPERATURE = 0.7
const DEFAULT_THRESHOLD = 0.8
const DEFAULT_REASONING = 'none'

const REASONING_OPTIONS = [
  { label: '关闭', value: 'none' },
  { label: '低', value: 'low' },
  { label: '中', value: 'medium' },
  { label: '高', value: 'high' }
] as const

export function ChatParamsPanel(): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const settings = useStore($sessionSettings)
  const gateway = useStore($gateway)

  // 读取当前会话的温度、压缩阈值与推理等级（若无会话级覆盖则回退到默认值）
  const tempValue = typeof settings.temperature === 'number' ? settings.temperature : DEFAULT_TEMPERATURE

  const thresholdValue =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const reasoningValue = typeof settings.reasoning_effort === 'string' ? settings.reasoning_effort : DEFAULT_REASONING

  const [temp, setTemp] = useState(tempValue)
  const [threshold, setThreshold] = useState(thresholdValue)
  const [reasoning, setReasoning] = useState(reasoningValue)

  // 当会话切换或 settings 更新时同步本地状态
  useEffect(() => {
    setTemp(tempValue)
  }, [tempValue])

  useEffect(() => {
    setThreshold(thresholdValue)
  }, [thresholdValue])

  useEffect(() => {
    setReasoning(reasoningValue)
  }, [reasoningValue])

  // 防抖持久化到后端 session.set_settings
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const syncSettingsToGateway = useCallback(
    (patch: { context_compression_threshold?: number; reasoning_effort?: string; temperature?: number }) => {
      if (!sessionId || !gateway || gateway.connectionState !== 'open') {
        return
      }

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        debounceTimerRef.current = null
        void gateway
          .request('session.set_settings', {
            session_id: sessionId,
            settings: patch
          })
          .catch(() => {
            /* 尽力而为 */
          })
      }, 350)
    },
    [sessionId, gateway]
  )

  useEffect(
    () => () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
    },
    []
  )

  const handleTempChange = (val: number) => {
    const rounded = Math.round(val * 100) / 100
    setTemp(rounded)
    updateSessionSetting('temperature', rounded)
    syncSettingsToGateway({
      context_compression_threshold: threshold,
      reasoning_effort: reasoning,
      temperature: rounded
    })
  }

  const handleThresholdChange = (val: number) => {
    const rounded = Math.min(1.0, Math.max(0.3, Math.round(val * 100) / 100))
    setThreshold(rounded)
    updateSessionSetting('context_compression_threshold', rounded)
    syncSettingsToGateway({ context_compression_threshold: rounded, reasoning_effort: reasoning, temperature: temp })
  }

  const handleReasoningChange = (val: string) => {
    setReasoning(val)
    updateSessionSetting('reasoning_effort', val)
    syncSettingsToGateway({ context_compression_threshold: threshold, reasoning_effort: val, temperature: temp })
  }

  const tempPct = Math.round(temp * 100)
  const thresholdPct = Math.round(threshold * 100)

  return (
    <div className="w-full flex flex-col gap-2 rounded-xl border border-white/8 bg-white/[0.03] p-2.5 text-left shadow-inner">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-white/70">
          <SlidersHorizontal className="size-3.5 text-accent" />
          <span className="text-[11px] font-medium tracking-wide">对话参数</span>
        </div>
        <span className="text-[9px] text-white/30">当前会话</span>
      </div>

      {/* 温度调节 */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-white/60" title="较低更严谨精准，较高更具创造力">
            采样温度
          </span>
          <span className="font-mono text-white/90 font-medium">{temp.toFixed(2)}</span>
        </div>
        <div className="flex items-center gap-2">
          <input
            aria-label="采样温度"
            className="sa-slider h-1 w-full cursor-pointer accent-accent"
            max={1}
            min={0}
            onChange={e => handleTempChange(Number(e.target.value))}
            step={0.05}
            style={{ '--sa-slider-fill': `${tempPct}%` } as React.CSSProperties}
            type="range"
            value={temp}
          />
        </div>
      </div>

      {/* 压缩阈值调节 */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-white/60" title="上下文占用到该比例时触发历史自动压缩">
            压缩阈值
          </span>
          <span className="font-mono text-white/90 font-medium">{thresholdPct}%</span>
        </div>
        <div className="flex items-center gap-2">
          <input
            aria-label="压缩阈值"
            className="sa-slider h-1 w-full cursor-pointer accent-accent"
            max={1.0}
            min={0.3}
            onChange={e => handleThresholdChange(Number(e.target.value))}
            step={0.05}
            style={{ '--sa-slider-fill': `${((threshold - 0.3) / 0.7) * 100}%` } as React.CSSProperties}
            type="range"
            value={threshold}
          />
        </div>
      </div>

      {/* 推理等级调节 */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-white/60" title="模型思考链推理强度。none 关闭，low/medium/high 逐级加深">
            推理等级
          </span>
          <span className="text-white/90 font-medium text-[10px]">
            {REASONING_OPTIONS.find(o => o.value === reasoning)?.label ?? '关闭'}
          </span>
        </div>
        <div className="grid grid-cols-4 gap-0.5 rounded-lg border border-white/10 bg-white/5 p-0.5">
          {REASONING_OPTIONS.map(opt => {
            const active = reasoning === opt.value

            return (
              <button
                className={cn(
                  'rounded-md py-0.5 text-[10px] font-medium transition cursor-pointer text-center',
                  active ? 'bg-accent text-white shadow-xs' : 'text-white/50 hover:bg-white/10 hover:text-white/90'
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
    </div>
  )
}
