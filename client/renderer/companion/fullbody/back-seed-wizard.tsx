import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { resolvePortraitUrl } from '@/companion/avatar-image'
import { INPUT_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { HistoryGallery, PortraitLightbox } from '@/companion/onboarding/onboarding-components'
import { MAX_APPEARANCE } from '@/companion/persona'

const HISTORY_CAP = 5

interface BackSeedWizardProps {
  avatarId: number
  /** 确认背面（或选择仅用正面图降级）后由调用方切换渲染模式并关闭向导。 */
  onConfirm: () => void
  onCancel: () => void
}

interface HistoryEntry {
  rawUrl: string | null
  previewUrl: string
}

// 3D 升级前的背面种子图确认向导（DESIGN §5.5）：背面视图只在用户明确选择 3D 时
// 才生成——2D 路径不需要它。参考图恒为已确认的正面种子，生成失败可降级为仅正面图提交。
export function BackSeedWizard({ avatarId, onConfirm, onCancel }: BackSeedWizardProps): React.ReactElement {
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [feedback, setFeedback] = useState('')
  const [backUrl, setBackUrl] = useState<string | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [history, setHistory] = useState<{ entries: HistoryEntry[]; idx: number }>({ entries: [], idx: 0 })
  const overlayRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(true)
  const generatingRef = useRef(false)

  useInteractiveRegion('back-seed-wizard', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  useEffect(() => {
    // StrictMode 开发态会卸载重挂一次，cleanup 已把标记置 false——重挂时必须复位，
    // 否则 boot 里所有 mountedRef 守卫全部失效，向导永久停在加载态。
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  const generateBack = useCallback(
    async (feedbackText: string): Promise<void> => {
      if (generatingRef.current) {
        return
      }

      generatingRef.current = true
      setLoading(true)
      setHint(null)
      setFailed(false)

      try {
        const res = await window.spiritagent.api<{ seed_back_url?: string | null }>({
          path: `/api/companion/avatar/${avatarId}/fullbody/back`,
          method: 'POST',
          body: { feedback: feedbackText.trim() || undefined }
        })

        const rawBack = res?.seed_back_url || null
        const resolved = rawBack ? await resolvePortraitUrl(rawBack) : null

        if (!rawBack || !resolved) {
          throw new Error('生成背面全身图失败，请稍后重试')
        }

        if (!mountedRef.current) {
          return
        }

        setBackUrl(resolved)
        setHistory(prev => {
          const entries = [...prev.entries, { rawUrl: rawBack, previewUrl: resolved }]
          const capped = entries.length > HISTORY_CAP ? entries.slice(entries.length - HISTORY_CAP) : entries

          return { entries: capped, idx: capped.length - 1 }
        })
      } catch (err) {
        if (!mountedRef.current) {
          return
        }

        setFailed(true)
        setHint(err instanceof Error ? err.message : '生成背面全身图失败，请重试')
      } finally {
        generatingRef.current = false

        if (mountedRef.current) {
          setLoading(false)
        }
      }
    },
    [avatarId]
  )

  // 打开时水合已有背面种子（存量用户直接复用）；没有则立即生成——切到 3D 的意图已经明确。
  useEffect(() => {
    const boot = async (): Promise<void> => {
      let existingRaw: string | null = null

      try {
        const res = await window.spiritagent.api<{ seed_back_url?: string | null }>({
          path: '/api/companion/avatar'
        })

        existingRaw = res?.seed_back_url || null
      } catch {
        // 拉取失败不阻塞向导：当作没有背面，直接走生成路径。
      }

      if (!mountedRef.current) {
        return
      }

      if (existingRaw) {
        const resolved = await resolvePortraitUrl(existingRaw)

        if (mountedRef.current && resolved) {
          setBackUrl(resolved)
          setHistory({ entries: [{ rawUrl: existingRaw, previewUrl: resolved }], idx: 0 })
          setLoading(false)

          return
        }
      }

      await generateBack('')
    }

    void boot()
  }, [generateBack])

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

  const onSelectHistoryEntry = (idx: number): void => {
    const entry = history.entries[idx]

    if (entry) {
      setHistory(prev => ({ ...prev, idx }))
      setBackUrl(entry.previewUrl)
    }
  }

  const regenerate = (): void => {
    void generateBack(feedback)
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      ref={overlayRef}
      style={{ pointerEvents: 'auto' }}
    >
      <div className="flex max-h-[85vh] w-full max-w-sm flex-col overflow-hidden rounded-2xl border border-white/15 bg-black/80 text-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h2 className="text-sm font-semibold">升级 3D：确认背面立绘</h2>
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
          <p className="text-[11px] leading-relaxed text-white/55">
            3D 建模以你确认的正面立绘为基准补一张背面视图，供云端多视角建模使用；不满意可微调重绘。
          </p>

          <div className="relative mx-auto mt-3 flex aspect-[9/16] max-h-[320px] w-auto items-center justify-center overflow-hidden rounded-xl border border-white/15 bg-black/40 group">
            {backUrl ? (
              <button
                aria-label="放大查看"
                className="relative block h-full w-full cursor-zoom-in overflow-hidden border-0 bg-transparent p-0"
                onClick={() => setZoomUrl(backUrl)}
                type="button"
              >
                <img alt="背面全身立绘" className="h-full w-full object-cover" src={backUrl} />
              </button>
            ) : (
              <div className="text-xs text-white/40">{loading ? '正在生成背面立绘…' : '暂无背面立绘'}</div>
            )}
          </div>

          {history.entries.length > 1 && (
            <div className="mt-2">
              <HistoryGallery
                entries={history.entries.map(entry => ({ url: entry.previewUrl }))}
                onSelect={onSelectHistoryEntry}
                selectedIdx={history.idx}
              />
            </div>
          )}

          <div className="mt-3">
            <textarea
              className={`${INPUT_CLASS} text-xs`}
              disabled={loading}
              maxLength={MAX_APPEARANCE}
              onChange={e => setFeedback(e.target.value)}
              placeholder="对背面立绘有微调要求？例如：发型细节、背部服饰/配饰…（可留空直接确认）"
              rows={2}
              value={feedback}
            />
          </div>

          {hint && <p className="mt-2 text-xs text-rose-300/90">{hint}</p>}

          {failed && !backUrl && (
            <div className="mt-3 flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              <span className="text-white/60">背面立绘生成失败</span>
              <div className="flex gap-2">
                <button
                  className="rounded-full border border-white/25 px-3 py-1 text-white/80 transition hover:bg-white/10 disabled:opacity-40"
                  disabled={loading}
                  onClick={regenerate}
                  type="button"
                >
                  重试
                </button>
                <button
                  className="rounded-full border border-white/25 px-3 py-1 text-white/80 transition hover:bg-white/10"
                  onClick={onConfirm}
                  type="button"
                >
                  仅用正面图生成 3D
                </button>
              </div>
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <button
              className="text-white/60 transition hover:text-white disabled:opacity-40"
              disabled={loading}
              onClick={onCancel}
              type="button"
            >
              取消
            </button>
            <div className="flex items-center gap-3">
              <button
                className="text-white/70 transition hover:text-white disabled:opacity-40"
                disabled={loading || !backUrl}
                onClick={regenerate}
                type="button"
              >
                微调重绘
              </button>
              <button
                className="rounded-full bg-white/90 px-4 py-1.5 font-medium text-black transition hover:bg-white disabled:opacity-40"
                disabled={loading || !backUrl}
                onClick={onConfirm}
                type="button"
              >
                确认，切换到 3D
              </button>
            </div>
          </div>
        </div>
      </div>

      {zoomUrl && <PortraitLightbox name="背面全身立绘" onClose={() => setZoomUrl(null)} url={zoomUrl} />}
    </div>
  )
}
