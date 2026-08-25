import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { log } from '@/shared/lib/log'

import { OutfitWizard } from './outfit-wizard'
import { $outfits, activateOutfit, deleteOutfit, hydrateWardrobe } from './wardrobe-store'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  splitting: '生成中…',
  failed: '生成失败',
  expired: '已过期'
}

// 衣柜（DESIGN §8 伙伴设置模块）：浏览外观、切换穿着、设计新装。
// 仅 2D 渲染模式下渲染（3D 模型不随服装变）；穿着中的外观不可删，保证至少一套。
export function WardrobeSection(): React.ReactElement {
  const outfits = useStore($outfits)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)

  useEffect(() => {
    void hydrateWardrobe()
  }, [])

  const withBusy = (id: number, action: () => Promise<unknown>): void => {
    setBusyId(id)
    void action().finally(() => setBusyId(null))
  }

  const retrySplit = async (id: number): Promise<void> => {
    try {
      await window.spiritagent.api({ path: `/api/companion/outfits/${id}/confirm`, method: 'POST' })
      await hydrateWardrobe()
    } catch (err) {
      log.warn('wardrobe', 'retry split failed', err)
    }
  }

  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-white/80">衣柜</p>

      {outfits.length === 0 ? (
        <p className="text-xs text-white/40">还没有就绪的 2D 形象，生成 2D 动画资产后即可换装。</p>
      ) : (
        <div className="space-y-1.5">
          {outfits.map(outfit => {
            const statusLabel = STATUS_LABELS[outfit.status] ?? ''
            const deletable = !outfit.active && outfit.status !== 'splitting'

            return (
              <div
                className={`flex items-center gap-2.5 rounded-lg border px-2.5 py-2 text-xs transition ${outfit.active ? 'border-white/60 bg-white/15' : 'border-white/10 bg-white/5'}`}
                key={outfit.id}
              >
                {outfit.fullbodyUrl ? (
                  <img
                    alt={outfit.name}
                    className="size-10 shrink-0 rounded border border-white/15 object-cover"
                    src={outfit.fullbodyUrl}
                  />
                ) : (
                  <div className="size-10 shrink-0 rounded border border-white/10 bg-white/5" />
                )}

                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">
                    {outfit.name}
                    {outfit.status === 'splitting' && outfit.pendingWear && (
                      <span className="ml-1.5 text-[10px] text-white/40">完成后自动穿上</span>
                    )}
                    {statusLabel && (
                      <span
                        className={`ml-1.5 text-[10px] ${outfit.status === 'failed' ? 'text-rose-300/80' : 'text-white/40'}`}
                      >
                        {statusLabel}
                      </span>
                    )}
                  </p>
                  {outfit.description && (
                    <p className="mt-0.5 truncate text-[10px] text-white/40">{outfit.description}</p>
                  )}
                </div>

                <div className="flex shrink-0 gap-2">
                  {outfit.status === 'ready' && !outfit.active && (
                    <button
                      className="text-white/60 transition hover:text-white disabled:opacity-40"
                      disabled={busyId === outfit.id}
                      onClick={() => withBusy(outfit.id, () => activateOutfit(outfit.id))}
                      type="button"
                    >
                      穿着
                    </button>
                  )}
                  {outfit.status === 'failed' && (
                    <button
                      className="text-white/60 transition hover:text-white disabled:opacity-40"
                      disabled={busyId === outfit.id}
                      onClick={() => withBusy(outfit.id, () => retrySplit(outfit.id))}
                      type="button"
                    >
                      重试
                    </button>
                  )}
                  {outfit.active && <span className="text-emerald-400">✓ 穿着中</span>}
                  {deletable && (
                    <button
                      className="text-white/40 transition hover:text-rose-300 disabled:opacity-40"
                      disabled={busyId === outfit.id}
                      onClick={() => withBusy(outfit.id, () => deleteOutfit(outfit.id))}
                      type="button"
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <button
        className="mt-2 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white/70 transition hover:bg-white/10"
        onClick={() => setWizardOpen(true)}
        type="button"
      >
        设计新外观 ✨
      </button>
      <p className="mt-1.5 text-[10px] text-white/30">
        换装仅支持 2D 动画版（3D 模式下隐藏）；穿着中的外观不可删除；每小时最多生成一套。
      </p>

      {wizardOpen && (
        <OutfitWizard
          onCancel={() => setWizardOpen(false)}
          onConfirmed={() => {
            setWizardOpen(false)
            void hydrateWardrobe()
          }}
        />
      )}
    </div>
  )
}
