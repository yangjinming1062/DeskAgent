import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $chatSessionId, $sessionSettings, updateSessionSetting } from '@/companion/chat-store'
import { SlidersHorizontal } from '@/shared/lib/icons'
import { $gateway } from '@/shared/store/gateway'

const DEFAULT_TEMPERATURE = 0.7
const DEFAULT_THRESHOLD = 0.8

export function ChatParamsPanel(): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const settings = useStore($sessionSettings)
  const gateway = useStore($gateway)

  // 读取当前会话的温度与压缩阈值（若无会话级覆盖则回退到默认值）
  const tempValue = typeof settings.temperature === 'number' ? settings.temperature : DEFAULT_TEMPERATURE

  const thresholdValue =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const [temp, setTemp] = useState(tempValue)
  const [threshold, setThreshold] = useState(thresholdValue)

  // 当会话切换或 settings 更新时同步本地滑块状态
  useEffect(() => {
    setTemp(tempValue)
  }, [tempValue])

  useEffect(() => {
    setThreshold(thresholdValue)
  }, [thresholdValue])

  // 防抖持久化到后端 session.set_settings
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const syncSettingsToGateway = useCallback(
    (patch: { temperature?: number; context_compression_threshold?: number }) => {
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
    syncSettingsToGateway({ temperature: rounded, context_compression_threshold: threshold })
  }

  const handleThresholdChange = (val: number) => {
    const rounded = Math.round(val * 100) / 100
    setThreshold(rounded)
    updateSessionSetting('context_compression_threshold', rounded)
    syncSettingsToGateway({ temperature: temp, context_compression_threshold: rounded })
  }

  const tempPct = Math.round(temp * 100)
  const thresholdPct = Math.round(threshold * 100)

  return (
    <div className="w-full mt-3 flex flex-col gap-2.5 rounded-xl border border-white/8 bg-white/[0.03] p-3 text-left shadow-inner">
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
            max={0.95}
            min={0.5}
            onChange={e => handleThresholdChange(Number(e.target.value))}
            step={0.05}
            style={{ '--sa-slider-fill': `${((threshold - 0.5) / 0.45) * 100}%` } as React.CSSProperties}
            type="range"
            value={threshold}
          />
        </div>
      </div>
    </div>
  )
}
