import { useStore } from '@nanostores/react'
import type React from 'react'
import { useMemo } from 'react'

import { SUPPORTED_RIG_TYPES } from '@/companion/3d/rig'

import {
  $activeClip,
  $embeddedClips,
  $searchQuery,
  $selectedCategory,
  $selectedRig,
  CATEGORY_LABELS,
  RIG_LABELS,
  selectClip
} from './store'
import type { RigType } from './types'

export function ClipList(): React.JSX.Element {
  const selectedRig = useStore($selectedRig)
  const selectedCategory = useStore($selectedCategory)
  const searchQuery = useStore($searchQuery)
  const activeClip = useStore($activeClip)
  const embeddedClips = useStore($embeddedClips)

  const allClips = embeddedClips

  const { categories, filteredClips } = useMemo(() => {
    const catSet = new Set<string>(['all'])

    for (const clip of allClips) {
      catSet.add(clip.category)
    }

    const query = searchQuery.trim().toLowerCase()

    const filtered = allClips.filter(clip => {
      if (selectedCategory !== 'all' && clip.category !== selectedCategory) {
        return false
      }

      if (query) {
        const nameMatch = clip.name.toLowerCase().includes(query)
        const catMatch = (CATEGORY_LABELS[clip.category] || clip.category).toLowerCase().includes(query)
        const tagMatch = clip.tags?.some(t => t.toLowerCase().includes(query))

        return nameMatch || catMatch || tagMatch
      }

      return true
    })

    return { categories: [...catSet], filteredClips: filtered }
  }, [allClips, selectedCategory, searchQuery])

  return (
    <aside className="flex h-full w-84 shrink-0 flex-col border-r border-slate-800 bg-slate-900/95 text-slate-200 backdrop-blur-md">
      {/* 顶部骨骼 Rig 选择栏 */}
      <div className="flex flex-col gap-2.5 border-b border-slate-800 p-3.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold tracking-wider text-slate-400 uppercase">骨骼体系 Rig Type</span>
          <span className="rounded bg-sky-500/10 px-2 py-0.5 text-[11px] font-medium text-sky-400">
            {RIG_LABELS[selectedRig]?.en || selectedRig}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          {SUPPORTED_RIG_TYPES.map(rig => {
            const meta = RIG_LABELS[rig]
            const isSelected = selectedRig === rig

            return (
              <button
                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-all ${
                  isSelected
                    ? 'bg-sky-500 text-white shadow-md shadow-sky-500/20'
                    : 'bg-slate-800/70 text-slate-300 hover:bg-slate-800 hover:text-white'
                }`}
                key={rig}
                onClick={() => $selectedRig.set(rig as RigType)}
                type="button"
              >
                <span>{meta.icon}</span>
                <span className="truncate">{meta.label}</span>
              </button>
            )
          })}
        </div>

        {/* 搜索框 */}
        <div className="relative mt-1">
          <input
            className="w-full rounded-lg border border-slate-700/80 bg-slate-950/80 px-3 py-1.5 pr-8 text-xs text-slate-100 placeholder-slate-500 transition-colors focus:border-sky-500 focus:outline-none"
            onChange={e => $searchQuery.set(e.target.value)}
            placeholder="搜索动画名称、标签..."
            type="text"
            value={searchQuery}
          />
          {searchQuery && (
            <button
              className="absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-200"
              onClick={() => $searchQuery.set('')}
              type="button"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* 分类标签横向滑动条 */}
      <div className="no-scrollbar flex gap-1.5 overflow-x-auto border-b border-slate-800/80 px-3.5 py-2">
        {categories.map(cat => {
          const isSelected = selectedCategory === cat
          const label = CATEGORY_LABELS[cat] || cat

          return (
            <button
              className={`flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
                isSelected
                  ? 'bg-sky-500/20 text-sky-300 ring-1 ring-sky-400/50'
                  : 'bg-slate-800/60 text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
              key={cat}
              onClick={() => $selectedCategory.set(cat)}
              type="button"
            >
              <span>{label}</span>
            </button>
          )
        })}
      </div>

      {/* 动画列表 */}
      <div className="flex-1 overflow-y-auto p-2.5">
        {filteredClips.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-slate-500">
            <span className="mb-2 text-2xl">🔍</span>
            <p className="text-xs">未找到符合条件的动画片段</p>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {filteredClips.map(clip => {
              const isActive = activeClip?.name === clip.name && activeClip?.category === clip.category

              return (
                <button
                  className={`group relative flex flex-col rounded-xl border p-2.5 text-left transition-all ${
                    isActive
                      ? 'border-sky-500/80 bg-sky-950/40 shadow-lg shadow-sky-950/50 ring-1 ring-sky-500/40'
                      : 'border-slate-800/80 bg-slate-850/50 hover:border-slate-700 hover:bg-slate-800/60'
                  }`}
                  key={clip.id}
                  onClick={() => selectClip(clip)}
                  type="button"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {isActive ? (
                        <div className="flex h-4 w-4 items-center justify-center">
                          <span className="relative flex h-2.5 w-2.5">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-75" />
                            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-sky-500" />
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-slate-500 transition-colors group-hover:text-slate-300">▶</span>
                      )}
                      <span
                        className={`font-mono text-xs font-semibold ${isActive ? 'text-sky-200' : 'text-slate-200'}`}
                      >
                        {clip.name}
                      </span>
                    </div>

                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[10px] text-slate-400">{clip.duration.toFixed(1)}s</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${
                          clip.loop
                            ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                            : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                        }`}
                      >
                        {clip.loop ? '循环' : '单次'}
                      </span>
                    </div>
                  </div>

                  {/* 标签与骨骼数 */}
                  <div className="mt-1.5 flex items-center justify-between text-[10px] text-slate-400">
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-400">
                      {CATEGORY_LABELS[clip.category] || clip.category}
                    </span>

                    {clip.tags && clip.tags.length > 0 && (
                      <div className="flex gap-1 overflow-hidden truncate">
                        {clip.tags.slice(0, 2).map((t, idx) => (
                          <span className="text-slate-500" key={idx}>
                            #{t}
                          </span>
                        ))}
                      </div>
                    )}

                    <span className="text-slate-500">{clip.trackCount} tracks</span>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* 底部统计 */}
      <div className="border-t border-slate-800/80 px-3.5 py-2 text-center text-[11px] text-slate-500">
        显示 {filteredClips.length} / {allClips.length} 个动作片段
      </div>
    </aside>
  )
}
