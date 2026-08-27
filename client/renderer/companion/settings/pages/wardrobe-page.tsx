import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { PortraitLightbox } from '@/companion/onboarding/onboarding-components'
import { useOutfitDesignSession } from '@/companion/wardrobe/design-session'
import { $outfits, activateOutfit, deleteOutfit, hydrateWardrobe } from '@/companion/wardrobe/wardrobe-store'
import { Check, FileImage, Pencil, Plus, Send, Trash2 } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_ICON, BTN_PRIMARY, CHIP, HINT_TEXT, INPUT_CLASS, Spinner } from '@/shared/panel'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  splitting: '切分中…',
  failed: '切分失败',
  expired: '已过期'
}

// 衣柜三栏页（DESIGN §8）：左侧外观列表，右上大图预览，右下类聊天的设计区。
// 设计流程不再弹窗——描述 / 参考图 / 微调反馈 / 确认入柜都在右半侧完成。
export function WardrobePage(): React.JSX.Element {
  const outfits = useStore($outfits)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [designing, setDesigning] = useState(false)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)

  // 确认入柜后重拉列表——新装进入切分态并自动穿上。
  const session = useOutfitDesignSession(() => {
    setDesigning(false)
    setText('')
    void hydrateWardrobe()
  })

  useEffect(() => {
    void hydrateWardrobe()
  }, [])

  // 选中项被删除 / 列表刷新后兜底回落到穿着中（或第一项）。
  useEffect(() => {
    if (!outfits.some(o => o.id === selectedId)) {
      setSelectedId(outfits.find(o => o.active)?.id ?? outfits[0]?.id ?? null)
    }
  }, [outfits, selectedId])

  useEffect(() => {
    messagesRef.current?.scrollTo?.({ top: messagesRef.current.scrollHeight })
  }, [session.messages])

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

  const startDesign = (): void => {
    session.reset()
    setDesigning(true)
    setSelectedId(null)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const selected = outfits.find(o => o.id === selectedId) ?? null
  const previewUrl = designing ? session.draft?.previewUrl : (selected?.fullbodyUrl ?? null)

  const sendText = (): void => {
    // 生成进行中会话内部会拒绝——此时不清空输入，避免丢字。
    if (session.busy || (!text.trim() && !session.refImage)) {
      return
    }

    session.send(text)
    setText('')
  }

  return (
    <div className="flex min-h-0 flex-1">
      {/* 左：外观列表 */}
      <div className="flex w-60 shrink-0 flex-col border-r border-white/10">
        <div className="flex items-center justify-between gap-2 px-3 pt-3.5 pb-2">
          <h2 className="text-sm font-semibold text-white">衣柜</h2>
          <button className={cn(BTN_PRIMARY, 'h-7 px-2.5')} onClick={startDesign} type="button">
            <Plus className="size-3.5" />
            设计新外观
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2.5 pb-3">
          {outfits.length === 0 ? (
            <p className="px-1 pt-2 text-xs text-white/40">还没有就绪的 2D 形象，生成 2D 动画资产后即可换装。</p>
          ) : (
            outfits.map(outfit => {
              const statusLabel = STATUS_LABELS[outfit.status] ?? ''
              const deletable = !outfit.active && outfit.status !== 'splitting'
              const isActiveCard = !designing && selectedId === outfit.id

              return (
                <div
                  className={`group relative flex cursor-pointer items-center gap-2.5 rounded-xl border px-2.5 py-2 text-xs transition ${
                    isActiveCard
                      ? 'border-[#6c8aff]/50 bg-[#6c8aff]/10'
                      : 'border-transparent hover:border-white/10 hover:bg-white/5'
                  }`}
                  key={outfit.id}
                  onClick={() => {
                    setDesigning(false)
                    setSelectedId(outfit.id)
                  }}
                >
                  {outfit.fullbodyUrl ? (
                    <img
                      alt={outfit.name}
                      className="size-10 shrink-0 rounded-lg border border-white/10 object-cover"
                      src={outfit.fullbodyUrl}
                    />
                  ) : (
                    <div className="size-10 shrink-0 rounded-lg border border-white/10 bg-white/5" />
                  )}

                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1 truncate font-medium text-white/90">
                      {outfit.name}
                      {outfit.active && <Check className="size-3.5 shrink-0 text-emerald-400" />}
                      {statusLabel && (
                        <span className={cn(CHIP, 'shrink-0 px-1.5 py-0 text-[9px]')}>{statusLabel}</span>
                      )}
                    </p>
                    <p className="mt-0.5 truncate text-[10px] text-white/40">
                      {outfit.status === 'splitting' && outfit.pendingWear ? '切分完成后自动穿上' : outfit.description}
                    </p>
                  </div>

                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100">
                    {outfit.status === 'draft' && (
                      <button
                        aria-label="继续设计"
                        className={cn(BTN_ICON, 'size-6')}
                        onClick={e => {
                          e.stopPropagation()
                          session.adoptDraft(outfit.id, outfit.fullbodyUrl ?? '')
                          setDesigning(true)
                          setSelectedId(null)
                        }}
                        title="继续设计这套草稿"
                        type="button"
                      >
                        <Pencil className="size-3.5" />
                      </button>
                    )}
                    {outfit.status === 'ready' && !outfit.active && (
                      <button
                        aria-label="穿着"
                        className={cn(BTN_ICON, 'size-6')}
                        disabled={busyId === outfit.id}
                        onClick={e => {
                          e.stopPropagation()
                          withBusy(outfit.id, () => activateOutfit(outfit.id))
                        }}
                        title="穿上这套外观"
                        type="button"
                      >
                        <Check className="size-3.5" />
                      </button>
                    )}
                    {outfit.status === 'failed' && (
                      <button
                        className={cn(BTN_GHOST, 'h-6 px-1.5')}
                        disabled={busyId === outfit.id}
                        onClick={e => {
                          e.stopPropagation()
                          withBusy(outfit.id, () => retrySplit(outfit.id))
                        }}
                        type="button"
                      >
                        重试
                      </button>
                    )}
                    {deletable && (
                      <button
                        aria-label="删除"
                        className={cn(BTN_ICON, 'size-6 hover:text-rose-300')}
                        disabled={busyId === outfit.id}
                        onClick={e => {
                          e.stopPropagation()
                          withBusy(outfit.id, () => deleteOutfit(outfit.id))
                        }}
                        title="删除这套外观"
                        type="button"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* 右：上预览 / 下设计区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
          {previewUrl ? (
            <button
              className="relative block max-h-full max-w-full cursor-zoom-in overflow-hidden rounded-xl border border-white/10 bg-black/30"
              onClick={() => setZoomUrl(previewUrl)}
              type="button"
            >
              <img alt="外观立绘" className="max-h-full max-w-full object-contain" src={previewUrl} />
            </button>
          ) : (
            <div className="grid place-items-center text-center">
              <div>
                <div className="text-xs text-white/50">
                  {designing ? '描述想要的着装，生成后在这里预览' : '选择左侧外观查看大图，或开始设计新外观'}
                </div>
                <div className="mt-1 text-[10px] text-white/30">服装、发型、配饰都可以换；五官与体型保持不变</div>
              </div>
            </div>
          )}

          {designing && session.busy && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/40">
              <Spinner className="size-5" />
              <span className="text-xs text-white/60">生成中…</span>
            </div>
          )}
        </div>

        <div className="flex h-56 shrink-0 flex-col border-t border-white/10">
          {!designing ? (
            <div className="grid flex-1 place-items-center px-6 text-center">
              <div>
                <p className="text-xs text-white/50">用一段描述（可附参考图）让伙伴换上新装</p>
                <button className={cn(BTN_PRIMARY, 'mt-3')} onClick={startDesign} type="button">
                  开始新设计
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3 text-xs" ref={messagesRef}>
                {session.messages.length === 0 && (
                  <p className="text-white/35">
                    描述想要的着装，例如「水手服换成米白色针织毛衣和棕色长裙，发型改为低马尾」；也可以附一张参考图。每小时最多生成一套。
                  </p>
                )}
                {session.messages.map(m =>
                  m.role === 'user' ? (
                    <div className="flex justify-end" key={m.id}>
                      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-[#6c8aff]/25 px-3 py-1.5 text-white/90">
                        {m.imageUrl && (
                          <img
                            alt="参考图"
                            className="mb-1 size-16 rounded-lg border border-white/15 object-cover"
                            src={m.imageUrl}
                          />
                        )}
                        {m.text && <p className="whitespace-pre-wrap break-words">{m.text}</p>}
                      </div>
                    </div>
                  ) : (
                    <div className="flex justify-start" key={m.id}>
                      <div
                        className={cn(
                          'max-w-[85%] rounded-2xl rounded-bl-sm border px-3 py-1.5',
                          m.tone === 'error'
                            ? 'border-rose-400/25 bg-rose-500/10 text-rose-200'
                            : 'border-white/10 bg-white/5 text-white/70'
                        )}
                      >
                        {m.text}
                      </div>
                    </div>
                  )
                )}
              </div>

              {session.draft && (
                <div className="flex items-center gap-2 border-t border-white/8 px-4 py-2">
                  <button
                    className={cn(BTN_PRIMARY, 'h-7')}
                    disabled={session.busy}
                    onClick={() => void session.confirm()}
                    type="button"
                  >
                    {session.busy ? '处理中…' : '确认入柜并穿上'}
                  </button>
                  <button
                    className={BTN_GHOST}
                    disabled={session.busy}
                    onClick={() => setDesigning(false)}
                    type="button"
                  >
                    放弃草稿
                  </button>
                  <span className={cn(HINT_TEXT, 'ml-auto')}>
                    确认后进行 2D 切分，完成后自动穿上（期间保持当前外观）
                  </span>
                </div>
              )}

              {session.refImage && !session.draft && (
                <div className="flex items-center gap-2 border-t border-white/8 px-4 py-1.5 text-[11px] text-white/60">
                  <img
                    alt="参考图"
                    className="size-8 rounded border border-white/15 object-cover"
                    src={session.refImage.previewUrl}
                  />
                  已附参考图
                  <button
                    className="text-white/40 transition hover:text-white"
                    onClick={session.clearRefImage}
                    type="button"
                  >
                    移除
                  </button>
                </div>
              )}

              <div className="flex items-end gap-2 border-t border-white/10 p-3">
                <textarea
                  className={cn(INPUT_CLASS, 'min-h-[38px] flex-1 resize-none')}
                  disabled={session.busy}
                  onChange={e => setText(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      sendText()
                    }
                  }}
                  placeholder={
                    session.draft
                      ? '想微调哪里？继续描述…（Enter 发送）'
                      : '描述想要的着装…（Enter 发送，Shift+Enter 换行）'
                  }
                  ref={inputRef}
                  rows={2}
                  value={text}
                />
                {!session.draft && (
                  <button
                    aria-label="附参考图"
                    className={cn(BTN_ICON, 'h-9 w-9 shrink-0 self-end')}
                    disabled={session.busy}
                    onClick={() => void session.attachRefImage()}
                    title="附参考图（可选，仅首次生成）"
                    type="button"
                  >
                    <FileImage />
                  </button>
                )}
                <button
                  aria-label="发送"
                  className={cn(BTN_PRIMARY, 'h-9 w-9 shrink-0 self-end px-0')}
                  disabled={session.busy || (!text.trim() && !session.refImage)}
                  onClick={sendText}
                  type="button"
                >
                  <Send className="size-4" />
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {zoomUrl && <PortraitLightbox name="外观立绘" onClose={() => setZoomUrl(null)} url={zoomUrl} />}
    </div>
  )
}
