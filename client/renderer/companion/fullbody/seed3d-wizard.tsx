import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { resolvePortraitUrl } from '@/companion/avatar-image'
import { INPUT_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { HistoryGallery, PortraitLightbox } from '@/companion/onboarding/onboarding-components'
import { MAX_APPEARANCE } from '@/companion/persona'

const HISTORY_CAP = 5

interface Seed3dWizardProps {
  avatarId: number
  /** 供应商是否按多图模式消费种子图（决定背面阶段是否存在）。 */
  supportsMultiview: boolean
  /** 确认 3D 种子（或选择仅用正面图降级）后由调用方切换渲染模式并关闭向导。 */
  onConfirm: () => void
  onCancel: () => void
}

type Stage = 'front' | 'back'

interface StageState {
  rawUrl: string | null
  previewUrl: string | null
  entries: { rawUrl: string; previewUrl: string }[]
  idx: number
  loading: boolean
  failed: boolean
}

const EMPTY_STAGE: StageState = { rawUrl: null, previewUrl: null, entries: [], idx: 0, loading: false, failed: false }

const STAGE_META: Record<
  Stage,
  {
    endpoint: string
    field: 'seed_front_3d_url' | 'seed_back_url'
    title: string
    hint: string
    alt: string
    placeholder: string
    genFail: string
    loadFail: string
  }
> = {
  front: {
    endpoint: '/fullbody/front-3d',
    field: 'seed_front_3d_url',
    title: '升级 3D：确认 3D 正面立绘',
    hint: '3D 建模需要标准站姿（A-pose）与 3D 画风的正面图，以你的形象头像为基准生成；不满意可微调重绘。',
    alt: '3D 正面全身立绘',
    placeholder: '对 3D 正面立绘有微调要求？例如：姿势细节、服饰纹理…（可留空直接下一步）',
    genFail: '生成 3D 正面立绘失败，请稍后重试',
    loadFail: '正在生成 3D 正面立绘…'
  },
  back: {
    endpoint: '/fullbody/back',
    field: 'seed_back_url',
    title: '升级 3D：确认背面立绘',
    hint: '3D 多视角建模以刚确认的 3D 正面立绘为基准补一张背面视图；不满意可微调重绘。',
    alt: '背面全身立绘',
    placeholder: '对背面立绘有微调要求？例如：发型细节、背部服饰/配饰…（可留空直接确认）',
    genFail: '生成背面立绘失败，请稍后重试',
    loadFail: '正在生成背面立绘…'
  }
}

// 3D 升级前的种子图确认向导（DESIGN §5.5）：3D 画风与站姿要求与 2D 立绘不同——先以已确认
// 的 2D 正面种子为基准生成 A-pose 的 3D 正面立绘，多视角供应商再从它派生背面立绘，两者都
// 只在用户明确选择 3D 时生成。生成失败可降级为仅正面图提交。
export function Seed3dWizard({
  avatarId,
  supportsMultiview,
  onConfirm,
  onCancel
}: Seed3dWizardProps): React.ReactElement {
  const [stage, setStage] = useState<Stage>('front')

  const [stages, setStages] = useState<Record<Stage, StageState>>({
    front: { ...EMPTY_STAGE, loading: true },
    back: EMPTY_STAGE
  })

  const [feedback, setFeedback] = useState<Record<Stage, string>>({ front: '', back: '' })
  const [hint, setHint] = useState<string | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(true)
  const generatingRef = useRef(false)

  useInteractiveRegion('seed3d-wizard', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  useEffect(() => {
    // StrictMode 开发态会卸载重挂一次，cleanup 已把标记置 false——重挂时必须复位，
    // 否则 boot 里所有 mountedRef 守卫全部失效，向导永久停在加载态。
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  const patchStage = useCallback((key: Stage, patch: Partial<StageState>): void => {
    setStages(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }, [])

  const generate = useCallback(
    async (key: Stage, feedbackText: string): Promise<boolean> => {
      if (generatingRef.current) {
        return false
      }

      const meta = STAGE_META[key]
      generatingRef.current = true
      patchStage(key, { loading: true, failed: false })
      setHint(null)

      try {
        const res = await window.spiritagent.api<{ seed_front_3d_url?: string | null; seed_back_url?: string | null }>({
          path: `/api/companion/avatar/${avatarId}${meta.endpoint}`,
          method: 'POST',
          body: { feedback: feedbackText.trim() || undefined }
        })

        const raw = res?.[meta.field] || null
        const resolved = raw ? await resolvePortraitUrl(raw) : null

        if (!raw || !resolved) {
          throw new Error(meta.genFail)
        }

        if (!mountedRef.current) {
          return true
        }

        setStages(prev => {
          const cur = prev[key]
          const entries = [...cur.entries, { rawUrl: raw, previewUrl: resolved }]
          const capped = entries.length > HISTORY_CAP ? entries.slice(entries.length - HISTORY_CAP) : entries

          return {
            ...prev,
            [key]: {
              ...cur,
              rawUrl: raw,
              previewUrl: resolved,
              entries: capped,
              idx: capped.length - 1,
              loading: false,
              failed: false
            }
          }
        })

        // 正面重绘会使后端已派生的背面种子失效，本地同步作废（进入背面阶段时自动重生成）
        if (key === 'front') {
          setStages(prev => ({ ...prev, back: EMPTY_STAGE }))
        }

        return true
      } catch (err) {
        if (!mountedRef.current) {
          return false
        }

        patchStage(key, { loading: false, failed: true })
        setHint(err instanceof Error ? err.message : meta.genFail)

        return false
      } finally {
        generatingRef.current = false
      }
    },
    [avatarId, patchStage]
  )

  // 打开时水合已有 3D 种子（存量用户直接复用）；没有则立即生成——切到 3D 的意图已经明确。
  useEffect(() => {
    const boot = async (): Promise<void> => {
      let existingFront: string | null = null
      let existingBack: string | null = null

      try {
        const res = await window.spiritagent.api<{ seed_front_3d_url?: string | null; seed_back_url?: string | null }>({
          path: '/api/companion/avatar'
        })

        existingFront = res?.seed_front_3d_url || null
        existingBack = res?.seed_back_url || null
      } catch {
        // 拉取失败不阻塞向导：当作没有 3D 种子，直接走生成路径。
      }

      if (!mountedRef.current) {
        return
      }

      if (existingFront) {
        const resolved = await resolvePortraitUrl(existingFront)

        if (mountedRef.current && resolved) {
          setStages(prev => ({
            ...prev,
            front: {
              ...prev.front,
              rawUrl: existingFront,
              previewUrl: resolved,
              entries: [{ rawUrl: existingFront, previewUrl: resolved }],
              idx: 0,
              loading: false,
              failed: false
            }
          }))

          if (supportsMultiview) {
            setStage('back')
          }

          if (existingBack) {
            const backResolved = await resolvePortraitUrl(existingBack)

            if (mountedRef.current && backResolved) {
              setStages(prev => ({
                ...prev,
                back: {
                  ...prev.back,
                  rawUrl: existingBack,
                  previewUrl: backResolved,
                  entries: [{ rawUrl: existingBack, previewUrl: backResolved }],
                  idx: 0,
                  loading: false,
                  failed: false
                }
              }))

              return
            }
          }

          return
        }
      }

      await generate('front', '')
    }

    void boot()
  }, [generate, supportsMultiview])

  // 进入背面阶段时若无背面种子（首次进入或正面重绘作废）则立即生成；失败后由用户手动重试，不自动循环
  useEffect(() => {
    if (stage === 'back' && !stages.back.previewUrl && !stages.back.loading && !stages.back.failed) {
      void generate('back', '')
    }
  }, [stage, stages.back.previewUrl, stages.back.loading, stages.back.failed, generate])

  // Esc 关闭向导；灯箱打开时让灯箱自己的 Esc 生效，不连带关掉整个向导。
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !zoomUrl) {
        onCancel()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, zoomUrl])

  const onSelectHistoryEntry = (key: Stage, idx: number): void => {
    const entry = stages[key].entries[idx]

    if (entry) {
      patchStage(key, { idx, rawUrl: entry.rawUrl, previewUrl: entry.previewUrl })
    }
  }

  const regenerate = (key: Stage): void => {
    void generate(key, feedback[key])
  }

  const current = stages[stage]
  const meta = STAGE_META[stage]

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      ref={overlayRef}
      style={{ pointerEvents: 'auto' }}
    >
      <div className="flex max-h-[85vh] w-full max-w-sm flex-col overflow-hidden rounded-2xl border border-white/15 bg-black/80 text-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h2 className="text-sm font-semibold">
            {meta.title}
            {supportsMultiview && (
              <span className="ml-1.5 text-[11px] font-normal text-white/40">
                {stage === 'front' ? '1 / 2' : '2 / 2'}
              </span>
            )}
          </h2>
          <button
            aria-label="关闭"
            className="text-white/50 transition hover:text-white"
            onClick={onCancel}
            type="button"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4 text-xs">
          <p className="text-[11px] leading-relaxed text-white/55">{meta.hint}</p>

          <div className="relative mx-auto mt-3 flex aspect-[9/16] max-h-[320px] w-auto items-center justify-center overflow-hidden rounded-xl border border-white/15 bg-black/40 group">
            {current.previewUrl ? (
              <button
                aria-label="放大查看"
                className="relative block h-full w-full cursor-zoom-in overflow-hidden border-0 bg-transparent p-0"
                onClick={() => setZoomUrl(current.previewUrl)}
                type="button"
              >
                <img alt={meta.alt} className="h-full w-full object-cover" src={current.previewUrl ?? undefined} />
              </button>
            ) : (
              <div className="text-xs text-white/40">{current.loading ? meta.loadFail : '暂无立绘'}</div>
            )}
          </div>

          {current.entries.length > 1 && (
            <div className="mt-2">
              <HistoryGallery
                entries={current.entries.map(entry => ({ url: entry.previewUrl }))}
                onSelect={idx => onSelectHistoryEntry(stage, idx)}
                selectedIdx={current.idx}
              />
            </div>
          )}

          <div className="mt-3">
            <textarea
              className={`${INPUT_CLASS} text-xs`}
              disabled={current.loading}
              maxLength={MAX_APPEARANCE}
              onChange={e => setFeedback(prev => ({ ...prev, [stage]: e.target.value }))}
              placeholder={meta.placeholder}
              rows={2}
              value={feedback[stage]}
            />
          </div>

          {hint && <p className="mt-2 text-xs text-rose-300/90">{hint}</p>}

          {current.failed && !current.previewUrl && (
            <div className="mt-3 flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              <span className="text-white/60">{stage === 'front' ? '3D 正面立绘生成失败' : '背面立绘生成失败'}</span>
              <div className="flex gap-2">
                <button
                  className="rounded-full border border-white/25 px-3 py-1 text-white/80 transition hover:bg-white/10 disabled:opacity-40"
                  disabled={current.loading}
                  onClick={() => regenerate(stage)}
                  type="button"
                >
                  重试
                </button>
                {stage === 'back' && (
                  <button
                    className="rounded-full border border-white/25 px-3 py-1 text-white/80 transition hover:bg-white/10"
                    onClick={onConfirm}
                    type="button"
                  >
                    仅用正面图生成 3D
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <button
              className="text-white/60 transition hover:text-white disabled:opacity-40"
              disabled={current.loading}
              onClick={stage === 'back' ? () => setStage('front') : onCancel}
              type="button"
            >
              {stage === 'back' ? '返回正面' : '取消'}
            </button>
            <div className="flex items-center gap-3">
              <button
                className="text-white/70 transition hover:text-white disabled:opacity-40"
                disabled={current.loading || !current.previewUrl}
                onClick={() => regenerate(stage)}
                type="button"
              >
                微调重绘
              </button>
              {stage === 'front' && supportsMultiview ? (
                <button
                  className="rounded-full bg-white/90 px-4 py-1.5 font-medium text-black transition hover:bg-white disabled:opacity-40"
                  disabled={current.loading || !current.previewUrl}
                  onClick={() => setStage('back')}
                  type="button"
                >
                  下一步：背面立绘
                </button>
              ) : (
                <button
                  className="rounded-full bg-white/90 px-4 py-1.5 font-medium text-black transition hover:bg-white disabled:opacity-40"
                  disabled={current.loading || !current.previewUrl}
                  onClick={onConfirm}
                  type="button"
                >
                  确认，切换到 3D
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {zoomUrl && <PortraitLightbox name={meta.alt} onClose={() => setZoomUrl(null)} url={zoomUrl} />}
    </div>
  )
}
