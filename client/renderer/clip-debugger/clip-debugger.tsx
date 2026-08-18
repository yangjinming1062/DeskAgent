import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { ClipList } from './clip-list'
import { readGlbFile } from './model-loader'
import { ModelSourceModal } from './model-source-modal'
import { MorphInspector } from './morph-inspector'
import { PlaybackBar } from './playback-bar'
import {
  $customGlbBuffer,
  $modelStats,
  $viewportOptions,
  setBackground,
  toggleAxes,
  toggleGrid,
  toggleSkeleton,
  toggleWireframe
} from './store'
import type { ViewportBackground } from './types'
import { Viewport3D } from './viewport-3d'

export function ClipDebugger(): React.JSX.Element {
  const [showMorphPanel, setShowMorphPanel] = useState(false)
  const [showSourceModal, setShowSourceModal] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const viewportOpts = useStore($viewportOptions)
  const modelStats = useStore($modelStats)
  const customGlb = useStore($customGlbBuffer)

  // 处理本地模型文件上传
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]

    if (file) {
      const loaded = await readGlbFile(file)
      $customGlbBuffer.set(loaded)
    }
  }

  const bgOptions: { key: ViewportBackground; label: string }[] = [
    { key: 'studio', label: '深色演播厅' },
    { key: 'slate', label: '暗调板岩' },
    { key: 'midnight', label: '极夜蓝黑' },
    { key: 'light', label: '浅色明朗' },
    { key: 'transparent', label: '透明网格' }
  ]

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-950 text-slate-100 antialiased select-none font-sans">
      {/* 模型来源模态框 (后端拉取 / 远程 URL / 本地文件) */}
      <ModelSourceModal isOpen={showSourceModal} onClose={() => setShowSourceModal(false)} />

      {/* 隐藏的文件上传 input */}
      <input accept=".glb,.gltf" className="hidden" onChange={handleFileUpload} ref={fileInputRef} type="file" />

      {/* 顶部主导航栏 */}
      <header className="flex h-13 shrink-0 items-center justify-between border-b border-slate-800 bg-slate-900/90 px-4 backdrop-blur-md">
        {/* 左侧：标题与模型状态 */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500/20 text-lg">🎬</span>
            <div>
              <h1 className="text-xs font-bold tracking-tight text-white sm:text-sm">
                SpiritAgent 动画调试器
                <span className="ml-1.5 hidden text-[10px] font-normal text-sky-400 sm:inline">Direct Debug Mode</span>
              </h1>
              <p className="hidden text-[10px] text-slate-400 md:block">
                跳过 LLM 链路 · 本地全功能 3D 骨骼动画与表情动作检视
              </p>
            </div>
          </div>

          {/* 当前模型标记与切换按钮 */}
          <div className="hidden items-center gap-1.5 rounded-xl border border-slate-700/60 bg-slate-800/80 px-2.5 py-1 text-xs sm:flex">
            <span className="text-slate-400">模型:</span>
            <span className="max-w-44 truncate font-medium text-sky-300">
              {customGlb ? customGlb.name : modelStats?.name || '标准人偶 (Mannequin)'}
            </span>
            <button
              className="ml-1 rounded bg-sky-500/20 px-1.5 py-0.5 text-[11px] text-sky-300 hover:bg-sky-500/30"
              onClick={() => setShowSourceModal(true)}
              title="切换模型来源（拉取后端模型 / 输入 URL / 本地文件）"
              type="button"
            >
              切换
            </button>
            {customGlb && (
              <button
                className="ml-0.5 text-slate-400 hover:text-red-400"
                onClick={() => $customGlbBuffer.set(null)}
                title="还原为内置标准人偶"
                type="button"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* 右侧：辅助线切换、模型载入、表情面板切换 */}
        <div className="flex items-center gap-2">
          {/* 打开模型选择与后端拉取窗口 */}
          <button
            className="flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-950/40 px-2.5 py-1.5 text-xs font-semibold text-sky-200 transition-colors hover:border-sky-400 hover:bg-sky-900/60"
            onClick={() => setShowSourceModal(true)}
            title="选择模型来源（从后端拉取伴侣 GLB / 输入 URL / 上传文件）"
            type="button"
          >
            <span>☁️ 载入模型/后端</span>
          </button>

          {/* 辅助显示开关 */}
          <div className="flex items-center rounded-lg border border-slate-800 bg-slate-950/80 p-0.5">
            <button
              className={`rounded px-2 py-1 text-xs font-medium transition-all ${
                viewportOpts.showSkeleton ? 'bg-sky-500 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
              onClick={toggleSkeleton}
              title="骨骼骨架线 (SkeletonHelper)"
              type="button"
            >
              🦴 骨架
            </button>

            <button
              className={`rounded px-2 py-1 text-xs font-medium transition-all ${
                viewportOpts.showGrid ? 'bg-sky-500 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
              onClick={toggleGrid}
              title="地面网格 (Grid)"
              type="button"
            >
              📐 网格
            </button>

            <button
              className={`rounded px-2 py-1 text-xs font-medium transition-all ${
                viewportOpts.showAxes ? 'bg-sky-500 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
              onClick={toggleAxes}
              title="坐标轴 (Axes)"
              type="button"
            >
              🧭 轴线
            </button>

            <button
              className={`rounded px-2 py-1 text-xs font-medium transition-all ${
                viewportOpts.showWireframe ? 'bg-sky-500 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
              onClick={toggleWireframe}
              title="材质线框模式 (Wireframe)"
              type="button"
            >
              💡 线框
            </button>
          </div>

          {/* 背景选择 */}
          <select
            className="hidden rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-300 focus:outline-none lg:block"
            onChange={e => setBackground(e.target.value as ViewportBackground)}
            value={viewportOpts.background}
          >
            {bgOptions.map(opt => (
              <option key={opt.key} value={opt.key}>
                {opt.label}
              </option>
            ))}
          </select>

          {/* 表情调试面板折叠按钮 */}
          <button
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-all ${
              showMorphPanel
                ? 'bg-sky-500 text-white shadow-md shadow-sky-500/20'
                : 'border border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white'
            }`}
            onClick={() => setShowMorphPanel(!showMorphPanel)}
            type="button"
          >
            <span>🎭 表情调试</span>
          </button>
        </div>
      </header>

      {/* 主体区域：左侧动画清单 + 右侧 3D 视口 */}
      <main className="relative flex flex-1 overflow-hidden">
        {/* 左侧动画列表 */}
        <ClipList />

        {/* 右侧 3D 视口与浮动控件 */}
        <div className="relative flex-1">
          <Viewport3D />
          <PlaybackBar />
        </div>

        {/* 右侧抽屉式表情检视面板 */}
        {showMorphPanel && (
          <aside className="w-80 shrink-0 border-l border-slate-800 shadow-2xl transition-all">
            <MorphInspector />
          </aside>
        )}
      </main>
    </div>
  )
}
