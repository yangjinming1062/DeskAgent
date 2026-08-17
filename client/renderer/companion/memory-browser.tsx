import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { usePanelDrag } from '@/companion/hooks/use-panel-drag'
import { PERSONA_INPUT_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { notifyError } from '@/shared/store/notifications'

import { $memoryBrowserTab, type MemoryTab, setMemoryBrowserTab } from './memory-browser-store'

const MAX_AUTO_INJECT_CONTENT_CHARS = 500

interface MemoryRow {
  id: number
  context: string | null
  tags: string | null
  content: string | null
  created_at: string | null
  updated_at: string | null
}

interface MemoryCounts {
  recall: number
  auto_inject: number
  user_profile: number
  interaction_stats: number
  other: number
}

interface ListResponse {
  memories: MemoryRow[]
  counts: MemoryCounts
}

const inputClass = PERSONA_INPUT_CLASS

const chipClass =
  'rounded-full border border-white/15 bg-white/5 px-2.5 py-0.5 text-[11px] text-white/70 transition hover:bg-white/15'

const tabChip = (active: boolean): string => `${chipClass} ${active ? 'border-white/40 bg-white/20 text-white' : ''}`

const AUTO_INJECT_SLOTS: ReadonlyArray<{ context: string; label: string; hint: string }> = [
  {
    context: 'auto_inject:communication_style',
    label: 'communication style',
    hint: '回答怎么框定（语言、风格、是否 bullet）'
  },
  { context: 'auto_inject:rapport_state', label: 'rapport state', hint: '当前关系/熟悉度阶段' },
  {
    context: 'auto_inject:interaction_pattern',
    label: 'interaction pattern',
    hint: '典型使用节奏（夜间高频、短对话等）'
  },
  { context: 'auto_inject:mood_pattern', label: 'mood pattern', hint: '近期情绪倾向（模式，不是当下 mood）' },
  { context: 'auto_inject:relationship_signal', label: 'relationship signal', hint: '信任/打趣频率/正式度' }
]

export function MemoryBrowser({ onClose }: { onClose: () => void }): React.ReactElement {
  const tab = useStore($memoryBrowserTab)
  const { requestGateway } = useGatewayRequest()
  const panelRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('memory-browser', panelRef)
  const { bind: dragBind, storedOffset } = usePanelDrag('da.companion.memoryBrowserOffset', () => panelRef.current)

  const [rows, setRows] = useState<MemoryRow[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [draftById, setDraftById] = useState<Record<number, string>>({})
  const [savingById, setSavingById] = useState<Record<number, boolean>>({})
  // Bumped on every ``load`` invocation; ``load`` results that resolve
  // after a newer invocation started are discarded so a slow response
  // can't overwrite a faster one for the *new* tab.
  const loadIdRef = useRef(0)

  const load = useCallback(
    async (nextTab: MemoryTab) => {
      const id = ++loadIdRef.current
      setLoading(true)
      setHint(null)

      try {
        const res = await requestGateway<ListResponse>('memory.list', { kind: nextTab })

        if (loadIdRef.current !== id) {
          return
        }

        const list = res?.memories ?? []
        setRows(list)
        setCounts(res?.counts ?? null)
        setDraftById(Object.fromEntries(list.map(r => [r.id, r.content ?? ''])))
      } catch (err) {
        if (loadIdRef.current !== id) {
          return
        }

        setHint('加载失败')
        notifyError(err, '加载长期记忆失败')
      } finally {
        if (loadIdRef.current === id) {
          setLoading(false)
        }
      }
    },
    [requestGateway]
  )

  useEffect(() => {
    void load(tab)
  }, [tab, load])

  // Functional setState gives us the previous rows without a mirror ref —
  // the rollback branch restores both rows[i].content and draftById[i]
  // from the closure snapshot of `rows` taken at click time.
  const saveRecall = useCallback(
    async (id: number) => {
      const draft = draftById[id] ?? ''
      const prevContent = rows.find(r => r.id === id)?.content ?? ''
      setSavingById(s => ({ ...s, [id]: true }))

      try {
        await requestGateway('memory.update', { memory_id: id, content: draft })
        setRows(prev => prev.map(r => (r.id === id ? { ...r, content: draft } : r)))
      } catch (err) {
        setRows(prev => prev.map(r => (r.id === id ? { ...r, content: prevContent } : r)))
        setDraftById(d => ({ ...d, [id]: prevContent }))
        setHint('保存失败，已回滚')
        notifyError(err, '保存记忆失败')
      } finally {
        setSavingById(s => {
          const next = { ...s }
          delete next[id]

          return next
        })
      }
    },
    [draftById, requestGateway, rows]
  )

  const del = useCallback(
    async (id: number) => {
      const prevRows = rows
      setRows(prev => prev.filter(r => r.id !== id))

      try {
        await requestGateway('memory.delete', { memory_id: id })
      } catch (err) {
        setRows(prevRows)
        setHint('删除失败，已回滚')
        notifyError(err, '删除记忆失败')
      }
    },
    [requestGateway, rows]
  )

  const switchTab = (next: MemoryTab): void => {
    if (next === tab) {
      return
    }

    setMemoryBrowserTab(next)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center px-6 pb-10" style={{ pointerEvents: 'none' }}>
      <div
        className="flex h-[min(75vh,640px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/60 text-white shadow-2xl backdrop-blur-md"
        ref={panelRef}
        style={{
          pointerEvents: 'auto',
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined
        }}
      >
        <div
          className="flex cursor-grab items-center justify-between border-b border-white/10 px-4 py-3 active:cursor-grabbing"
          {...dragBind}
          title="拖动以移动面板"
        >
          <h2 className="text-sm font-semibold">长期记忆</h2>
          <button
            aria-label="关闭"
            className="text-white/50 transition hover:text-white"
            onClick={onClose}
            type="button"
          >
            ✕
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2 text-xs">
          <button className={tabChip(tab === 'recall')} onClick={() => switchTab('recall')} type="button">
            主动召回 · {counts?.recall ?? '…'}
          </button>
          <button className={tabChip(tab === 'auto_inject')} onClick={() => switchTab('auto_inject')} type="button">
            自动注入 · {counts?.auto_inject ?? '…'}
          </button>
          <span className="ml-auto text-[10px] text-white/30">
            {counts?.user_profile ?? '…'} 个 user_profile 由你独占
          </span>
        </div>

        {hint && <p className="px-4 pt-2 text-xs text-amber-300/80">{hint}</p>}

        <div className="flex-1 overflow-y-auto px-4 py-4 text-xs">
          {loading ? (
            <p className="text-white/40">加载中…</p>
          ) : tab === 'recall' ? (
            rows.length === 0 ? (
              <p className="text-white/40">还没有召回记忆。精灵会在对话中主动写下。</p>
            ) : (
              <div className="space-y-3">
                {rows.map(r => {
                  const tags = parseTags(r.tags)
                  const draft = draftById[r.id] ?? ''
                  const dirty = draft !== (r.content ?? '')
                  const saving = !!savingById[r.id]

                  return (
                    <div className="rounded-lg border border-white/10 bg-white/5 p-3" key={r.id}>
                      <textarea
                        className={`${inputClass} resize-none`}
                        disabled={saving}
                        onChange={e => setDraftById(d => ({ ...d, [r.id]: e.target.value }))}
                        rows={3}
                        value={draft}
                      />
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {tags.map(t => (
                          <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/70" key={t}>
                            {t}
                          </span>
                        ))}
                      </div>
                      <p className="mt-1 text-[10px] text-white/30">
                        {r.context ?? '—'} · 更新 {r.updated_at ?? '—'} · {draft.length} chars
                      </p>
                      <div className="mt-2 flex gap-2">
                        <button
                          className="rounded-lg border border-white/30 bg-white/15 px-3 py-1 text-[11px] font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
                          disabled={saving || !dirty}
                          onClick={() => void saveRecall(r.id)}
                          type="button"
                        >
                          {saving ? '保存中…' : '保存'}
                        </button>
                        <button
                          className="rounded-lg border border-white/15 bg-white/5 px-3 py-1 text-[11px] text-white/70 transition hover:bg-white/15 disabled:opacity-40"
                          disabled={saving}
                          onClick={() => void del(r.id)}
                          type="button"
                        >
                          删除
                        </button>
                        {!dirty && <span className="ml-1 text-[10px] text-white/30">已保存</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          ) : (
            <div className="space-y-3">
              <p className="text-[10px] text-white/40">
                自动注入段每次对话都默念一遍，LLM 写入时已限 {MAX_AUTO_INJECT_CONTENT_CHARS}{' '}
                字符。这里你可以查看或修正。
              </p>
              {AUTO_INJECT_SLOTS.map(slot => {
                const row = rows.find(r => r.context === slot.context)
                const draft = row ? (draftById[row.id] ?? row.content ?? '') : ''
                const dirty = !!row && draft !== (row.content ?? '')
                const overLimit = draft.length > MAX_AUTO_INJECT_CONTENT_CHARS
                const saving = !!row && !!savingById[row.id]

                return (
                  <div className="rounded-lg border border-white/10 bg-white/5 p-3" key={slot.context}>
                    <p className="mb-1 text-[11px] font-medium text-white/80">{slot.label}</p>
                    <p className="mb-1.5 text-[10px] text-white/40">{slot.hint}</p>
                    {row ? (
                      <>
                        <textarea
                          className={`${inputClass} resize-none`}
                          disabled={saving}
                          onChange={e => setDraftById(d => ({ ...d, [row.id]: e.target.value }))}
                          rows={2}
                          value={draft}
                        />
                        <p className="mt-1 text-[10px] text-white/30">
                          {draft.length} / {MAX_AUTO_INJECT_CONTENT_CHARS} chars · 更新 {row.updated_at ?? '—'}
                        </p>
                        <div className="mt-2 flex gap-2">
                          <button
                            className="rounded-lg border border-white/30 bg-white/15 px-3 py-1 text-[11px] font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
                            disabled={saving || !dirty || overLimit}
                            onClick={() => void saveRecall(row.id)}
                            type="button"
                          >
                            {saving ? '保存中…' : '保存'}
                          </button>
                          <button
                            className="rounded-lg border border-white/15 bg-white/5 px-3 py-1 text-[11px] text-white/70 transition hover:bg-white/15 disabled:opacity-40"
                            disabled={saving}
                            onClick={() => void del(row.id)}
                            type="button"
                          >
                            删除
                          </button>
                        </div>
                      </>
                    ) : (
                      <p className="text-[10px] text-white/30">（空）让精灵在对话中自然填入</p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function parseTags(raw: string | null): string[] {
  if (!raw) {
    return []
  }

  try {
    const parsed = JSON.parse(raw)

    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}
