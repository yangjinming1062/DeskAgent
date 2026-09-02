import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $activeClip,
  $playbackState,
  resetCamera,
  setCrossFadeDuration,
  setLoopMode,
  setPlaybackSpeed,
  stepFrame,
  togglePlay,
  triggerScrub
} from './store'
import type { PlaybackLoopMode } from './types'

const SPEEDS = [0.1, 0.25, 0.5, 1.0, 1.5, 2.0]

export function PlaybackBar(): React.JSX.Element {
  const playback = useStore($playbackState)
  const activeClip = useStore($activeClip)
  const [isScrubbing, setIsScrubbing] = useState(false)
  const [localScrubVal, setLocalScrubVal] = useState(0)

  // 全局空格键快捷键播放/暂停
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // 避免在输入框中触发
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)) {
        return
      }

      if (e.code === 'Space') {
        e.preventDefault()
        togglePlay()
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault()
        stepFrame(-0.05)
      } else if (e.code === 'ArrowRight') {
        e.preventDefault()
        stepFrame(0.05)
      }
    }

    window.addEventListener('keydown', handleKeyDown)

    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const duration = Math.max(0.1, playback.duration || activeClip?.duration || 1)
  const currentT = isScrubbing ? localScrubVal : Math.min(playback.currentTime, duration)

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value)
    setLocalScrubVal(val)
    triggerScrub(val)
  }

  return (
    <div className="absolute right-4 bottom-4 left-4 z-30 flex flex-col gap-2 rounded-2xl border border-slate-700/80 bg-slate-900/90 p-3.5 shadow-2xl shadow-slate-950/80 backdrop-blur-xl transition-all">
      {/* 上层：时间轴滑块与时间读数 */}
      <div className="flex items-center gap-3">
        <span className="w-14 text-right font-mono text-xs font-semibold text-sky-400">{currentT.toFixed(2)}s</span>

        <div className="relative flex-1">
          <input
            className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-slate-700/80 accent-sky-400 focus:outline-none"
            max={duration}
            min={0}
            onChange={handleSliderChange}
            onMouseDown={() => setIsScrubbing(true)}
            onMouseUp={() => setIsScrubbing(false)}
            onTouchEnd={() => setIsScrubbing(false)}
            onTouchStart={() => setIsScrubbing(true)}
            step={0.01}
            type="range"
            value={currentT}
          />
        </div>

        <span className="w-14 font-mono text-xs text-slate-400">{duration.toFixed(2)}s</span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800/80 pt-2.5">
        <div className="flex items-center gap-2">
          <button
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-800 text-xs text-slate-300 transition-colors hover:bg-slate-700 hover:text-white"
            onClick={() => stepFrame(-0.05)}
            title="后退一帧 (← 键)"
            type="button"
          >
            ⏮
          </button>

          <button
            className="flex h-9 items-center gap-1.5 rounded-xl bg-sky-500 px-4 text-xs font-semibold text-white shadow-md shadow-sky-500/25 transition-all hover:bg-sky-400 active:scale-95"
            onClick={togglePlay}
            title="播放/暂停 (空格键)"
            type="button"
          >
            <span>{playback.isPlaying ? '⏸ 暂停' : '▶ 播放'}</span>
          </button>

          <button
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-800 text-xs text-slate-300 transition-colors hover:bg-slate-700 hover:text-white"
            onClick={() => stepFrame(0.05)}
            title="前进一帧 (→ 键)"
            type="button"
          >
            ⏭
          </button>

          {activeClip && (
            <span className="ml-2 hidden font-mono text-xs font-medium text-slate-300 md:inline-block">
              {activeClip.name}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 rounded-lg bg-slate-950/60 p-1 border border-slate-800/80">
          <span className="px-1.5 text-[11px] text-slate-400">倍速:</span>
          {SPEEDS.map(spd => {
            const isSelected = playback.speed === spd

            return (
              <button
                className={`rounded px-2 py-0.5 font-mono text-xs font-medium transition-all ${
                  isSelected ? 'bg-sky-500 text-white' : 'text-slate-400 hover:text-slate-200'
                }`}
                key={spd}
                onClick={() => setPlaybackSpeed(spd)}
                type="button"
              >
                {spd}x
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-xs">
            <span className="text-[11px] text-slate-400">循环:</span>
            <select
              className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
              onChange={e => setLoopMode(e.target.value as PlaybackLoopMode)}
              value={playback.loopMode}
            >
              <option value="default">默认 ({activeClip?.loop ? '循环' : '单次'})</option>
              <option value="force-loop">强制循环 🔁</option>
              <option value="force-once">强制单次 🔂</option>
            </select>
          </div>

          {/* 过渡时长 Crossfade */}
          <div className="hidden items-center gap-1 text-xs sm:flex">
            <span className="text-[11px] text-slate-400">过渡:</span>
            <select
              className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
              onChange={e => setCrossFadeDuration(parseFloat(e.target.value))}
              value={playback.crossFadeDuration}
            >
              <option value="0">即时 (0s)</option>
              <option value="0.15">快速 (0.15s)</option>
              <option value="0.25">平滑 (0.25s)</option>
              <option value="0.5">慢速 (0.5s)</option>
            </select>
          </div>

          {/* 重置视角 */}
          <button
            className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800/80 px-2.5 py-1 text-xs text-slate-300 transition-colors hover:border-slate-600 hover:text-white"
            onClick={resetCamera}
            title="重置 3D 摄像机视角"
            type="button"
          >
            <span>🎯 归位</span>
          </button>
        </div>
      </div>
    </div>
  )
}
