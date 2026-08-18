import { useStore } from '@nanostores/react'
import type React from 'react'

import { $lipSyncAmp, $modelStats, $morphWeights, resetAllMorphs, setMorphWeight } from './store'

export function MorphInspector(): React.JSX.Element {
  const morphWeights = useStore($morphWeights)
  const lipSyncAmp = useStore($lipSyncAmp)
  const modelStats = useStore($modelStats)

  // 预制表情动作
  const applyPreset = (preset: 'neutral' | 'happy' | 'blink' | 'surprised' | 'angry' | 'sad') => {
    resetAllMorphs()

    switch (preset) {
      case 'happy':
        setMorphWeight('smile', 0.8)
        setMorphWeight('eye_squint', 0.4)

        break

      case 'blink':
        setMorphWeight('blink_left', 1.0)
        setMorphWeight('blink_right', 1.0)
        setMorphWeight('eyeBlinkLeft', 1.0)
        setMorphWeight('eyeBlinkRight', 1.0)

        break

      case 'surprised':
        setMorphWeight('mouth_open', 0.7)
        setMorphWeight('brow_up', 0.8)
        setMorphWeight('jawOpen', 0.7)

        break

      case 'angry':
        setMorphWeight('brow_down', 0.8)
        setMorphWeight('mouth_pout', 0.5)

        break

      case 'sad':
        setMorphWeight('brow_inner_up', 0.7)
        setMorphWeight('mouth_frown', 0.6)

        break

      case 'neutral':

      default:
        resetAllMorphs()

        break
    }
  }

  // 常见基础面部通道
  const commonMorphs = [
    { key: 'smile', label: '微笑 (Smile)' },
    { key: 'mouth_open', label: '张嘴 (Open Mouth)' },
    { key: 'blink_left', label: '左眨眼 (Blink L)' },
    { key: 'blink_right', label: '右眨眼 (Blink R)' },
    { key: 'brow_down', label: '皱眉 (Brow Down)' },
    { key: 'brow_up', label: '挑眉 (Brow Up)' }
  ]

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-slate-900/90 p-4 text-slate-200">
      <div className="mb-3 flex items-center justify-between border-b border-slate-800 pb-2">
        <div>
          <h3 className="text-xs font-bold text-sky-300">面部表情与 Blendshapes</h3>
          <p className="text-[10px] text-slate-400">结合骨骼动画实时调试表情与嘴型</p>
        </div>
        <button
          className="rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-300 transition-colors hover:bg-slate-700 hover:text-white"
          onClick={resetAllMorphs}
          type="button"
        >
          重置表情
        </button>
      </div>

      {/* 快捷预设表情 */}
      <div className="mb-4">
        <span className="mb-1.5 block text-[11px] font-semibold text-slate-400">快捷情绪预设</span>
        <div className="grid grid-cols-3 gap-1.5">
          <button
            className="rounded-lg bg-slate-800/80 px-2 py-1.5 text-xs text-slate-200 transition-colors hover:bg-slate-700"
            onClick={() => applyPreset('neutral')}
            type="button"
          >
            😐 自然
          </button>
          <button
            className="rounded-lg bg-slate-800/80 px-2 py-1.5 text-xs text-slate-200 transition-colors hover:bg-slate-700"
            onClick={() => applyPreset('happy')}
            type="button"
          >
            😄 开心
          </button>
          <button
            className="rounded-lg bg-slate-800/80 px-2 py-1.5 text-xs text-slate-200 transition-colors hover:bg-slate-700"
            onClick={() => applyPreset('blink')}
            type="button"
          >
            😉 眨眼
          </button>
          <button
            className="rounded-lg bg-slate-800/80 px-2 py-1.5 text-xs text-slate-200 transition-colors hover:bg-slate-700"
            onClick={() => applyPreset('surprised')}
            type="button"
          >
            😮 惊讶
          </button>
          <button
            className="rounded-lg bg-slate-800/80 px-2 py-1.5 text-xs text-slate-200 transition-colors hover:bg-slate-700"
            onClick={() => applyPreset('angry')}
            type="button"
          >
            😠 生气
          </button>
          <button
            className="rounded-lg bg-slate-800/80 px-2 py-1.5 text-xs text-slate-200 transition-colors hover:bg-slate-700"
            onClick={() => applyPreset('sad')}
            type="button"
          >
            🥺 委屈
          </button>
        </div>
      </div>

      {/* TTS 语音嘴型振幅测试 */}
      <div className="mb-4 rounded-xl border border-sky-900/50 bg-sky-950/20 p-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-semibold text-sky-200">🗣️ TTS 嘴型振幅模拟</span>
          <span className="font-mono text-xs text-sky-400">{(lipSyncAmp * 100).toFixed(0)}%</span>
        </div>
        <input
          className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-800 accent-sky-400"
          max={1}
          min={0}
          onChange={e => $lipSyncAmp.set(parseFloat(e.target.value))}
          step={0.01}
          type="range"
          value={lipSyncAmp}
        />
      </div>

      {/* 基础表情滑块 */}
      <div className="flex flex-col gap-2.5">
        <span className="text-[11px] font-semibold text-slate-400">表情通道调节</span>
        {commonMorphs.map(m => {
          const val = morphWeights[m.key] || 0

          return (
            <div className="flex flex-col gap-1" key={m.key}>
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-slate-300">{m.label}</span>
                <span className="font-mono text-slate-400">{val.toFixed(2)}</span>
              </div>
              <input
                className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-800 accent-sky-500"
                max={1}
                min={0}
                onChange={e => setMorphWeight(m.key, parseFloat(e.target.value))}
                step={0.01}
                type="range"
                value={val}
              />
            </div>
          )
        })}
      </div>

      {/* 模型元数据统计 */}
      {modelStats && (
        <div className="mt-6 border-t border-slate-800/80 pt-3 text-[10px] text-slate-400">
          <span className="mb-1 block font-semibold text-slate-300">模型几何统计</span>
          <div className="grid grid-cols-2 gap-1 font-mono">
            <span>顶点数: {modelStats.vertexCount.toLocaleString()}</span>
            <span>面数: {modelStats.triangleCount.toLocaleString()}</span>
            <span>骨骼数: {modelStats.boneCount}</span>
            <span>网格数: {modelStats.meshCount}</span>
          </div>
        </div>
      )}
    </div>
  )
}
