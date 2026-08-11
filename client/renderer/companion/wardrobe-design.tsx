import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $wardrobe,
  $wardrobeCandidates,
  $wardrobePreview,
  $wardrobeSelectedIdx,
  clearWardrobeCandidates,
  hydrateWardrobe,
  pushWardrobeCandidate,
  selectWardrobeCandidate,
  type WardrobeItem
} from '@/companion/3d/model-store'
import { PERSONA_INPUT_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { notify, notifyError } from '@/shared/store/notifications'

interface WardrobePreviewResponse {
  url: string
  prompt: string
  file_id: string
}

interface WardrobeDesignPanelProps {
  onClose: () => void
}

async function discardPreviewFiles(fileIds: string[]): Promise<void> {
  await Promise.all(
    fileIds.map(id =>
      window.deskagent
        .api<{ deleted: boolean }>({
          path: `/api/companion/wardrobe/preview/${id}`,
          method: 'DELETE'
        })
        .catch(() => undefined)
    )
  )
}

export function WardrobeDesignPanel({ onClose }: WardrobeDesignPanelProps): React.JSX.Element {
  const panelRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('wardrobe-design', panelRef)

  const candidates = useStore($wardrobeCandidates)
  const selectedIdx = useStore($wardrobeSelectedIdx)
  const wardrobe = useStore($wardrobe)
  const preview = useStore($wardrobePreview)

  const [description, setDescription] = useState('')
  const [feedback, setFeedback] = useState('')
  const [nameInput, setNameInput] = useState('')
  const [imagePreview, setImagePreview] = useState<string | null>(null)

  const [generating, setGenerating] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [statusMessage, setStatusMessage] = useState<{ type: 'info' | 'error' | 'success'; text: string } | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Ensure wardrobe catalog is hydrated
  useEffect(() => {
    void hydrateWardrobe()
  }, [])

  // Handle image file selection
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]

    if (!file) {
      return
    }

    if (!file.type.startsWith('image/')) {
      setStatusMessage({ type: 'error', text: '请选择有效的图片文件 (PNG / JPEG / WebP / GIF)' })

      return
    }

    if (file.size > 8 * 1024 * 1024) {
      setStatusMessage({ type: 'error', text: '图片大小不能超过 8MB' })

      return
    }

    const reader = new FileReader()

    reader.onload = () => {
      const result = reader.result as string
      setImagePreview(result)
      setStatusMessage(null)
    }

    reader.onerror = () => {
      setStatusMessage({ type: 'error', text: '读取参考图片失败' })
    }

    reader.readAsDataURL(file)
  }

  const removeReferenceImage = () => {
    setImagePreview(null)

    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // Handle generation of new texture candidate
  const handleGenerate = async () => {
    const desc = description.trim()

    if (!desc) {
      setStatusMessage({ type: 'error', text: '请输入服装/外观描述' })

      return
    }

    setGenerating(true)
    setStatusMessage(null)

    try {
      const res = await window.deskagent.api<WardrobePreviewResponse>({
        path: '/api/companion/wardrobe/preview',
        method: 'POST',
        body: {
          description: desc,
          image: imagePreview?.split(',')[1] ?? undefined,
          content_type: imagePreview?.match(/^data:([^;]+)/)?.[1] ?? undefined,
          feedback: feedback.trim() || undefined
        }
      })

      if (res?.url && res.file_id) {
        pushWardrobeCandidate({
          url: res.url,
          prompt: res.prompt,
          fileId: res.file_id,
          description: desc
        })
        setFeedback('')
        setNameInput(desc.slice(0, 16))
        setStatusMessage({ type: 'success', text: '贴图已生成，3D 模型已切换至预览！' })
      } else {
        setStatusMessage({ type: 'error', text: '生成返回数据不完整' })
      }
    } catch (err) {
      setStatusMessage({
        type: 'error',
        text: err instanceof Error ? err.message : '生成贴图失败，请稍后重试'
      })
      notifyError(err, '换装生成失败')
    } finally {
      setGenerating(false)
    }
  }

  // Handle candidate selection
  const handleSelectCandidate = (idx: number) => {
    selectWardrobeCandidate(idx)
    const selected = candidates[idx]

    if (selected) {
      setNameInput(selected.description.slice(0, 16))
    }
  }

  // Handle candidate confirmation
  const handleConfirm = async () => {
    const currentCandidate = candidates[selectedIdx]

    if (!currentCandidate) {
      setStatusMessage({ type: 'error', text: '未选中任何候选' })

      return
    }

    const finalName = nameInput.trim() || currentCandidate.description.slice(0, 16) || '定制装扮'
    setConfirming(true)
    setStatusMessage(null)

    try {
      const res = await window.deskagent.api<WardrobeItem>({
        path: '/api/companion/wardrobe/confirm',
        method: 'POST',
        body: {
          file_id: currentCandidate.fileId,
          name: finalName,
          prompt: currentCandidate.prompt
        }
      })

      if (res) {
        const otherIds = candidates
          .filter(c => c.fileId !== currentCandidate.fileId)
          .map(c => c.fileId)
          .filter(Boolean)

        clearWardrobeCandidates()

        void discardPreviewFiles(otherIds)

        setStatusMessage({ type: 'success', text: `「${finalName}」已成功保存并穿戴！` })
        notify({
          kind: 'info',
          title: '换装成功',
          message: `装扮「${finalName}」已加入衣柜并装备`
        })
      }
    } catch (err) {
      setStatusMessage({
        type: 'error',
        text: err instanceof Error ? err.message : '确认装扮失败'
      })
      notifyError(err, '确认换装失败')
    } finally {
      setConfirming(false)
    }
  }

  // Handle candidate discard
  const handleDiscard = () => {
    void discardAllPreviewFiles()
    setStatusMessage({ type: 'info', text: '已丢弃候选，恢复当前装扮' })
  }

  // Equip existing wardrobe item
  const handleEquipExisting = async (itemId: number) => {
    try {
      void discardAllPreviewFiles()
      await window.deskagent.api<WardrobeItem>({
        path: '/api/companion/wardrobe/equip',
        method: 'PUT',
        body: { item_id: itemId }
      })
      // wardrobe.updated WS event refreshes $wardrobe + $equippedItem
      setStatusMessage({ type: 'success', text: '已装备选中的外观' })
    } catch (err) {
      setStatusMessage({
        type: 'error',
        text: err instanceof Error ? err.message : '装备失败'
      })
    }
  }

  const handleClose = () => {
    if (preview) {
      void discardAllPreviewFiles()
    }

    onClose()
  }

  async function discardAllPreviewFiles(): Promise<void> {
    const ids = $wardrobeCandidates
      .get()
      .map(c => c.fileId)
      .filter(Boolean)

    clearWardrobeCandidates()

    await discardPreviewFiles(ids)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center px-4 pb-8" style={{ pointerEvents: 'none' }}>
      <div
        className="flex h-[min(84vh,700px)] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-white/15 bg-black/75 text-white shadow-2xl backdrop-blur-xl transition-all"
        ref={panelRef}
        style={{ pointerEvents: 'auto' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <span className="text-base">✨</span>
            <h2 className="text-sm font-semibold tracking-wide">换装设计 (Wardrobe Studio)</h2>
          </div>
          <button
            aria-label="关闭"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-white/50 transition hover:bg-white/10 hover:text-white"
            onClick={handleClose}
            type="button"
          >
            ✕
          </button>
        </div>

        {/* Status notice */}
        {statusMessage && (
          <div
            className={`border-b px-5 py-2 text-xs transition-colors ${
              statusMessage.type === 'error'
                ? 'border-red-500/30 bg-red-500/15 text-red-300'
                : statusMessage.type === 'success'
                  ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                  : 'border-blue-500/30 bg-blue-500/15 text-blue-300'
            }`}
          >
            {statusMessage.text}
          </div>
        )}

        {/* Scrollable Content */}
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-xs">
          {/* Section: Text + Image Input */}
          <div className="space-y-3 rounded-xl border border-white/10 bg-white/5 p-3.5">
            <div className="flex items-center justify-between">
              <label className="font-medium text-white/90" htmlFor="wardrobe-desc-input">
                服装描述
              </label>
              <span className="text-[10px] text-white/40">AI 生成 4 通道 PBR 贴图</span>
            </div>
            <textarea
              className={`${PERSONA_INPUT_CLASS} resize-none`}
              id="wardrobe-desc-input"
              onChange={e => setDescription(e.target.value)}
              placeholder="例如：赛博朋克霓虹黑色夹克、红色中式旗袍刺绣金纹、未来战术战斗服..."
              rows={2}
              value={description}
            />

            {/* Reference Image Uploader */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-white/70">参考图片（可选）</span>
                {imagePreview && (
                  <button
                    className="text-[10px] text-red-400/80 transition hover:text-red-300"
                    onClick={removeReferenceImage}
                    type="button"
                  >
                    移除图片
                  </button>
                )}
              </div>
              <input
                accept="image/png,image/jpeg,image/webp,image/gif"
                className="hidden"
                onChange={handleFileChange}
                ref={fileInputRef}
                type="file"
              />
              {imagePreview ? (
                <div className="flex items-center gap-3 rounded-lg border border-white/15 bg-white/5 p-2">
                  <img
                    alt="参考图预览"
                    className="h-12 w-12 rounded border border-white/20 object-cover"
                    src={imagePreview}
                  />
                  <div className="flex-1 truncate">
                    <p className="text-[11px] text-white/90">已上传参考图</p>
                    <p className="text-[10px] text-white/40">生成时将提取色彩与材质风格作为提示</p>
                  </div>
                  <button
                    className="rounded border border-white/15 bg-white/10 px-2 py-1 text-[10px] text-white/80 hover:bg-white/20"
                    onClick={() => fileInputRef.current?.click()}
                    type="button"
                  >
                    更换
                  </button>
                </div>
              ) : (
                <button
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-white/20 bg-white/[0.02] py-2.5 text-white/60 transition hover:border-white/40 hover:bg-white/[0.05] hover:text-white/80"
                  onClick={() => fileInputRef.current?.click()}
                  type="button"
                >
                  <span>🖼️</span>
                  <span>点击上传服装/材质参考图</span>
                </button>
              )}
            </div>

            {/* Regeneration Feedback (shown if candidates exist) */}
            {candidates.length > 0 && (
              <div className="space-y-1.5 pt-1">
                <label className="text-[11px] text-amber-200/90" htmlFor="wardrobe-feedback-input">
                  调整反馈（对上一版本的修改建议）
                </label>
                <textarea
                  className={`${PERSONA_INPUT_CLASS} resize-none border-amber-400/20 bg-amber-400/5 focus:border-amber-400/40`}
                  id="wardrobe-feedback-input"
                  onChange={e => setFeedback(e.target.value)}
                  placeholder="例如：颜色调深一些、金边更明显、减少反光度..."
                  rows={2}
                  value={feedback}
                />
              </div>
            )}

            {/* Generate Action Button */}
            <button
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-white/30 bg-white/15 py-2 text-xs font-medium text-white transition hover:bg-white/25 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={generating || !description.trim()}
              onClick={handleGenerate}
              type="button"
            >
              {generating ? (
                <>
                  <span className="inline-block animate-spin">⏳</span>
                  <span>正在设计生成贴图（约 5~15 秒）…</span>
                </>
              ) : (
                <>
                  <span>✨</span>
                  <span>{candidates.length > 0 ? '重新生成候选贴图' : '生成贴图'}</span>
                </>
              )}
            </button>
          </div>

          {/* Section: Candidate History & Live Preview Confirmation */}
          {candidates.length > 0 && (
            <div className="space-y-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3.5">
              <div className="flex items-center justify-between">
                <span className="font-medium text-emerald-200">候选回溯与预览（实时应用至精灵）</span>
                <span className="text-[10px] text-emerald-300/70">最多保存 3 个历史候选</span>
              </div>

              {/* Thumbnails Strip */}
              <div className="grid grid-cols-3 gap-2.5">
                {candidates.map((c, idx) => {
                  const isSelected = idx === selectedIdx

                  return (
                    <button
                      className={`relative flex flex-col items-center gap-1.5 rounded-lg border p-2 text-left transition ${
                        isSelected
                          ? 'border-emerald-400 bg-emerald-500/20 text-white shadow-lg ring-1 ring-emerald-400'
                          : 'border-white/15 bg-white/5 text-white/70 hover:border-white/30 hover:bg-white/10'
                      }`}
                      key={c.fileId || idx}
                      onClick={() => handleSelectCandidate(idx)}
                      type="button"
                    >
                      <div className="relative h-16 w-full overflow-hidden rounded">
                        <img alt={`候选 ${idx + 1}`} className="h-full w-full object-cover" src={c.url} />
                        {isSelected && (
                          <span className="absolute bottom-1 right-1 rounded bg-emerald-600/90 px-1 py-0.5 text-[8px] font-semibold text-white">
                            预览中
                          </span>
                        )}
                      </div>
                      <span className="line-clamp-1 w-full text-center text-[10px] font-medium">候选 {idx + 1}</span>
                    </button>
                  )
                })}
              </div>

              {/* Candidate Info and Actions */}
              {candidates[selectedIdx] && (
                <div className="space-y-2 pt-1">
                  <div className="flex items-center gap-2">
                    <label className="shrink-0 text-[11px] text-white/70" htmlFor="wardrobe-name-input">
                      装扮名称:
                    </label>
                    <input
                      className={`${PERSONA_INPUT_CLASS} py-1`}
                      id="wardrobe-name-input"
                      onChange={e => setNameInput(e.target.value)}
                      placeholder="为新装扮起个名字"
                      value={nameInput}
                    />
                  </div>

                  <div className="flex gap-2 pt-1">
                    <button
                      className="flex-1 rounded-lg border border-emerald-500/50 bg-emerald-600/30 py-2 text-xs font-semibold text-emerald-200 transition hover:bg-emerald-600/50 disabled:opacity-40"
                      disabled={confirming}
                      onClick={handleConfirm}
                      type="button"
                    >
                      {confirming ? '保存中…' : '✓ 确认入衣柜并穿戴'}
                    </button>
                    <button
                      className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/70 transition hover:bg-white/15 hover:text-white"
                      disabled={confirming}
                      onClick={handleDiscard}
                      type="button"
                    >
                      丢弃候选
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Section: Existing Wardrobe Items */}
          <div className="space-y-2.5 rounded-xl border border-white/10 bg-white/5 p-3.5">
            <div className="flex items-center justify-between">
              <span className="font-medium text-white/90">已拥有装扮 (Wardrobe)</span>
              <span className="text-[10px] text-white/40">点击直接换装</span>
            </div>

            {wardrobe.length === 0 ? (
              <p className="py-2 text-center text-xs text-white/40">衣柜为空</p>
            ) : (
              <div className="grid grid-cols-4 gap-2">
                {wardrobe.map(item => {
                  const isCurrent = !preview && item.equipped

                  return (
                    <button
                      className={`flex flex-col items-center gap-1 rounded-lg border p-1.5 text-center text-[10px] transition ${
                        isCurrent
                          ? 'border-white/80 bg-white/20 text-white ring-1 ring-white/60'
                          : 'border-white/15 bg-white/5 text-white/70 hover:border-white/30 hover:bg-white/10'
                      }`}
                      key={item.id}
                      onClick={() => void handleEquipExisting(item.id)}
                      type="button"
                    >
                      {item.texture_url ? (
                        <img
                          alt={item.name}
                          className="h-10 w-10 rounded border border-white/10 object-cover"
                          src={item.texture_url}
                        />
                      ) : (
                        <div className="grid h-10 w-10 place-items-center rounded bg-white/10 text-xs font-semibold text-white/80">
                          {item.name[0] || '装'}
                        </div>
                      )}
                      <span className="w-full truncate font-medium">{item.name}</span>
                      {isCurrent ? (
                        <span className="text-[8px] font-semibold text-emerald-300">已装备</span>
                      ) : (
                        <span className="text-[8px] text-white/30">{item.category === 'preset' ? '预设' : '定制'}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
