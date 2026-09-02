import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import {
  $modelTransform,
  $transformMode,
  resetCamera,
  resetTransform,
  rotatePreset,
  setModelPosition,
  setModelRotation,
  setModelScale,
  setTransformMode,
  triggerAutoCenter,
  triggerAutoGround,
  triggerNormalizeHeight
} from './store'
import type { TransformMode } from './types'

const MODES: ReadonlyArray<{ key: TransformMode; label: string; icon: string; title: string }> = [
  { key: 'view', label: '视角', icon: '🖐️', title: '视角旋转与漫游（鼠标右键平移/左键旋转/滚轮缩放）' },
  { key: 'translate', label: '移动', icon: '↔️', title: '显示 3D 位移坐标轴，可直接拖拽调整位置' },
  { key: 'rotate', label: '旋转', icon: '🔄', title: '显示 3D 旋转手柄，可直接拖拽调整朝向角度' },
  { key: 'scale', label: '缩放', icon: '📐', title: '显示 3D 缩放手柄，可直接拖拽调整模型大小' }
]

const AXES = ['x', 'y', 'z'] as const
type Axis = (typeof AXES)[number]

const AXIS_LABELS: Record<Axis, string> = { x: 'X', y: 'Y', z: 'Z' }
const AXIS_COLORS: Record<Axis, string> = { x: 'text-red-400', y: 'text-emerald-400', z: 'text-sky-400' }

export function TransformToolbar(): React.JSX.Element {
  const mode = useStore($transformMode)
  const transform = useStore($modelTransform)
  const [showDrawer, setShowDrawer] = useState(false)

  return (
    <div className="absolute top-4 left-4 z-20 flex flex-col items-start gap-2">
      {/* 顶部主工具栏 */}
      <div className="flex items-center gap-1.5 rounded-xl border border-slate-700/80 bg-slate-900/90 p-1.5 shadow-xl backdrop-blur-md">
        {/* 模式选择按钮组 */}
        <div className="flex items-center rounded-lg bg-slate-950/80 p-0.5">
          {MODES.map(m => (
            <button
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-semibold transition-all ${
                mode === m.key
                  ? 'bg-sky-500 text-white shadow-sm shadow-sky-500/30'
                  : 'text-slate-400 hover:bg-slate-800/80 hover:text-slate-200'
              }`}
              key={m.key}
              onClick={() => setTransformMode(m.key)}
              title={m.title}
              type="button"
            >
              <span>{m.icon}</span>
              <span>{m.label}</span>
            </button>
          ))}
        </div>

        <div className="h-4 w-px bg-slate-800" />

        {/* 快捷修复按钮组 */}
        <button
          className="flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/70 px-2 py-1 text-xs font-medium text-amber-300 transition-colors hover:border-amber-500/50 hover:bg-slate-700"
          onClick={() => rotatePreset('x', 90)}
          title="模型躺平时点击：绕 X 轴旋转 90° 快速立起"
          type="button"
        >
          <span>🧍</span>
          <span>立起(X+90°)</span>
        </button>

        <button
          className="flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/70 px-2 py-1 text-xs font-medium text-slate-200 transition-colors hover:border-sky-500/50 hover:bg-slate-700"
          onClick={() => rotatePreset('y', 180)}
          title="绕 Y 轴旋转 180° 水平转身面对镜头"
          type="button"
        >
          <span>🔄</span>
          <span>转身(180°)</span>
        </button>

        <button
          className="flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/70 px-2 py-1 text-xs font-medium text-emerald-300 transition-colors hover:border-emerald-500/50 hover:bg-slate-700"
          onClick={triggerAutoGround}
          title="自动计算包围盒，将角色双脚对齐至地面 Y=0"
          type="button"
        >
          <span>🦶</span>
          <span>贴地</span>
        </button>

        <button
          className="flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/70 px-2 py-1 text-xs font-medium text-slate-200 transition-colors hover:border-sky-500/50 hover:bg-slate-700"
          onClick={triggerAutoCenter}
          title="自动将模型水平中心对齐到世界坐标原点 (0,0)"
          type="button"
        >
          <span>🎯</span>
          <span>居中</span>
        </button>

        <button
          className="flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/70 px-2 py-1 text-xs font-medium text-slate-200 transition-colors hover:border-sky-500/50 hover:bg-slate-700"
          onClick={triggerNormalizeHeight}
          title="自适应缩放模型高度至标准人体 1.7 米"
          type="button"
        >
          <span>📏</span>
          <span>1.7m</span>
        </button>

        <button
          className="flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/70 px-2 py-1 text-xs font-medium text-slate-200 transition-colors hover:border-sky-500/50 hover:bg-slate-700"
          onClick={resetCamera}
          title="相机重置并聚焦居中角色"
          type="button"
        >
          <span>📷</span>
          <span>聚焦</span>
        </button>

        <div className="h-4 w-px bg-slate-800" />

        {/* 精确数值微调抽屉开关 */}
        <button
          className={`rounded-lg px-2 py-1 text-xs font-medium transition-all ${
            showDrawer ? 'bg-sky-500/20 text-sky-300' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
          }`}
          onClick={() => setShowDrawer(!showDrawer)}
          title="展开/收起坐标与角度微调数值面板"
          type="button"
        >
          <span>⚙️ 微调</span>
        </button>
      </div>

      {/* 精确数值调整抽屉面板 */}
      {showDrawer && (
        <div className="flex w-72 flex-col gap-3 rounded-xl border border-slate-700/80 bg-slate-900/95 p-3.5 shadow-2xl backdrop-blur-md">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <span className="text-xs font-bold text-slate-200">模型坐标与旋转微调</span>
            <button className="text-[11px] text-amber-400 hover:underline" onClick={resetTransform} type="button">
              全部重置
            </button>
          </div>

          {/* 位置 (X, Y, Z) */}
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-slate-400">位置偏移 (Position X/Y/Z, 米)</span>
            <div className="grid grid-cols-3 gap-1.5">
              {AXES.map(axis => (
                <div key={axis}>
                  <label className={`text-[10px] ${AXIS_COLORS[axis]}`}>{AXIS_LABELS[axis]}:</label>
                  <input
                    className="w-full rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 text-xs text-slate-100 focus:border-sky-500 focus:outline-none"
                    onChange={e =>
                      setModelPosition(
                        axis === 'x' ? parseFloat(e.target.value) || 0 : transform.position.x,
                        axis === 'y' ? parseFloat(e.target.value) || 0 : transform.position.y,
                        axis === 'z' ? parseFloat(e.target.value) || 0 : transform.position.z
                      )
                    }
                    step={0.05}
                    type="number"
                    value={Number(transform.position[axis].toFixed(3))}
                  />
                </div>
              ))}
            </div>
          </div>

          {/* 旋转 (X, Y, Z 角度) */}
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-slate-400">旋转角度 (Rotation X/Y/Z, 度)</span>
            <div className="grid grid-cols-3 gap-1.5">
              {AXES.map(axis => (
                <div key={axis}>
                  <label className={`text-[10px] ${AXIS_COLORS[axis]}`}>{AXIS_LABELS[axis]}:</label>
                  <input
                    className="w-full rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 text-xs text-slate-100 focus:border-sky-500 focus:outline-none"
                    onChange={e =>
                      setModelRotation(
                        axis === 'x' ? parseFloat(e.target.value) || 0 : transform.rotation.x,
                        axis === 'y' ? parseFloat(e.target.value) || 0 : transform.rotation.y,
                        axis === 'z' ? parseFloat(e.target.value) || 0 : transform.rotation.z
                      )
                    }
                    step={15}
                    type="number"
                    value={Math.round(transform.rotation[axis])}
                  />
                </div>
              ))}
            </div>
          </div>

          {/* 缩放比例 */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-slate-400">缩放倍率 (Scale)</span>
              <span className="font-mono text-xs text-sky-300">{transform.scale.toFixed(2)}x</span>
            </div>
            <input
              className="accent-sky-400"
              max={5.0}
              min={0.1}
              onChange={e => setModelScale(parseFloat(e.target.value) || 1.0)}
              step={0.05}
              type="range"
              value={transform.scale}
            />
          </div>
        </div>
      )}
    </div>
  )
}
