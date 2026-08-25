import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { pickAvatarImage, type PickedImage, resolvePortraitUrl } from '@/companion/avatar-image'
import { INPUT_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { PortraitLightbox } from '@/companion/onboarding/onboarding-components'
import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

interface OutfitWizardProps {
  /** 确认（或从失败态重试切分）后由调用方关闭向导；衣柜卡片转为「生成中…」。 */
  onConfirmed: () => void
  onCancel: () => void
}

interface DraftOutfit {
  id: number
  previewUrl: string
}

// 后端 4xx 错误体形如 409 {"detail":{"error":"…"}}——剥掉状态码前缀解析 JSON，
// 取 detail 里的公开文案；解析不了就用兜底。
function outfitErrMsg(err: unknown, fallback: string): string {
  const raw = unwrapIpcErrorMessage(err).replace(/^\d{3}\s*/, '')

  try {
    const parsed = JSON.parse(raw) as { detail?: { error?: unknown } }
    const backendError = parsed?.detail?.error

    if (typeof backendError === 'string' && backendError) {
      return backendError
    }
  } catch {
    /* 非预期形态，走兜底文案 */
  }

  return fallback
}

// 设计新外观向导：着装描述 + 可选参考图 → 草稿预览 → 微调重绘 → 确认入柜并自动穿着。
// 服装/发型可换、五官锁定——身份由后端用正面种子锚定，这里只收集着装意图。
export function OutfitWizard({ onConfirmed, onCancel }: OutfitWizardProps): React.ReactElement {
  const [description, setDescription] = useState('')
  const [refImage, setRefImage] = useState<PickedImage | null>(null)
  const [draft, setDraft] = useState<DraftOutfit | null>(null)
  const [feedback, setFeedback] = useState('')
  const [loading, setLoading] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(true)
  const generatingRef = useRef(false)

  useInteractiveRegion('outfit-wizard', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !zoomUrl) {
        onCancel()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, zoomUrl])

  const runGenerate = useCallback(
    async (mode: 'create' | 'regenerate'): Promise<void> => {
      if (generatingRef.current) {
        return
      }

      generatingRef.current = true
      setLoading(true)
      setHint(null)

      try {
        const res =
          mode === 'create'
            ? await window.spiritagent.api<{ id?: number; fullbody_url?: string }>({
                path: '/api/companion/outfits',
                method: 'POST',
                body: {
                  description: description.trim() || undefined,
                  image: refImage?.base64,
                  content_type: refImage?.contentType
                }
              })
            : await window.spiritagent.api<{ id?: number; fullbody_url?: string }>({
                path: `/api/companion/outfits/${draft?.id}/regenerate`,
                method: 'POST',
                body: { feedback: feedback.trim() || undefined }
              })

        const rawUrl = res?.fullbody_url || null
        const resolved = rawUrl ? await resolvePortraitUrl(rawUrl) : null

        if (!res?.id || !rawUrl || !resolved) {
          throw new Error('外观生成失败，请稍后重试')
        }

        if (!mountedRef.current) {
          return
        }

        setDraft({ id: res.id, previewUrl: resolved })
      } catch (err) {
        if (!mountedRef.current) {
          return
        }

        setHint(outfitErrMsg(err, '外观生成失败，请稍后重试'))
        log.warn('outfit-wizard', 'generation failed', err)
      } finally {
        generatingRef.current = false

        if (mountedRef.current) {
          setLoading(false)
        }
      }
    },
    [description, draft?.id, feedback, refImage]
  )

  const confirmDraft = useCallback(async (): Promise<void> => {
    if (!draft || generatingRef.current) {
      return
    }

    generatingRef.current = true
    setLoading(true)
    setHint(null)

    try {
      await window.spiritagent.api({ path: `/api/companion/outfits/${draft.id}/confirm`, method: 'POST' })

      if (mountedRef.current) {
        onConfirmed()
      }
    } catch (err) {
      if (!mountedRef.current) {
        return
      }

      setHint(outfitErrMsg(err, '确认失败，请稍后重试'))
      log.warn('outfit-wizard', 'confirm failed', err)
    } finally {
      generatingRef.current = false

      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }, [draft, onConfirmed])

  const pickRefImage = async (): Promise<void> => {
    const picked = await pickAvatarImage('选择服装参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    setRefImage(picked.image)
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      ref={overlayRef}
      style={{ pointerEvents: 'auto' }}
    >
      <div className="flex max-h-[85vh] w-full max-w-sm flex-col overflow-hidden rounded-2xl border border-white/15 bg-black/80 text-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h2 className="text-sm font-semibold">设计新外观</h2>
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
            描述想要的着装（服装、发型、配饰都可以换；五官与体型保持不变），也可以附一张参考图。每小时最多生成一套。
          </p>

          {!draft ? (
            <>
              <div className="mt-3">
                <textarea
                  className={`${INPUT_CLASS} text-xs`}
                  disabled={loading}
                  maxLength={500}
                  onChange={e => setDescription(e.target.value)}
                  placeholder="例如：水手服换成米白色针织毛衣和棕色长裙，发型改为低马尾…（有参考图时可留空）"
                  rows={3}
                  value={description}
                />
              </div>

              <div className="mt-2 flex items-center gap-2">
                <button
                  className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-white/70 transition hover:bg-white/10 disabled:opacity-40"
                  disabled={loading}
                  onClick={() => void pickRefImage()}
                  type="button"
                >
                  {refImage ? '更换参考图' : '附参考图（可选）'}
                </button>
                {refImage && (
                  <>
                    <img
                      alt="参考图"
                      className="size-9 rounded border border-white/15 object-cover"
                      src={refImage.previewUrl}
                    />
                    <button
                      className="text-white/50 transition hover:text-white"
                      onClick={() => setRefImage(null)}
                      type="button"
                    >
                      移除
                    </button>
                  </>
                )}
              </div>

              <button
                className="mt-3 w-full rounded-full bg-white/90 px-4 py-2 font-medium text-black transition hover:bg-white disabled:opacity-40"
                disabled={loading || (!description.trim() && !refImage)}
                onClick={() => void runGenerate('create')}
                type="button"
              >
                {loading ? '生成中…' : '生成外观'}
              </button>
            </>
          ) : (
            <>
              <div className="relative mx-auto mt-3 flex aspect-[9/16] max-h-[320px] w-auto items-center justify-center overflow-hidden rounded-xl border border-white/15 bg-black/40 group">
                <button
                  aria-label="放大查看"
                  className="relative block h-full w-full cursor-zoom-in overflow-hidden border-0 bg-transparent p-0"
                  onClick={() => setZoomUrl(draft.previewUrl)}
                  type="button"
                >
                  <img alt="新外观立绘" className="h-full w-full object-cover" src={draft.previewUrl} />
                </button>
              </div>

              <div className="mt-3">
                <textarea
                  className={`${INPUT_CLASS} text-xs`}
                  disabled={loading}
                  maxLength={500}
                  onChange={e => setFeedback(e.target.value)}
                  placeholder="对这套外观有微调要求？可再描述…（可留空直接确认）"
                  rows={2}
                  value={feedback}
                />
              </div>

              <p className="mt-2 text-[10px] text-white/35">
                确认后将进行 2D 动画切分，完成后自动穿上（期间保持当前外观）。
              </p>
            </>
          )}

          {hint && <p className="mt-2 text-xs text-rose-300/90">{hint}</p>}

          {draft && (
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
                  disabled={loading}
                  onClick={() => void runGenerate('regenerate')}
                  type="button"
                >
                  微调重绘
                </button>
                <button
                  className="rounded-full bg-white/90 px-4 py-1.5 font-medium text-black transition hover:bg-white disabled:opacity-40"
                  disabled={loading}
                  onClick={() => void confirmDraft()}
                  type="button"
                >
                  {loading ? '处理中…' : '确认，穿上它'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {zoomUrl && <PortraitLightbox name="新外观立绘" onClose={() => setZoomUrl(null)} url={zoomUrl} />}
    </div>
  )
}
