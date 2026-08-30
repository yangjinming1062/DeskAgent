import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $memoryBrowserTab, type MemoryTab, setMemoryBrowserTab } from '@/companion/memory-browser-store'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_SUBTLE, CHIP, CHIP_FILTER, CHIP_FILTER_ACTIVE, HINT_TEXT, INPUT_CLASS } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'

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

const AUTO_INJECT_SLOTS: ReadonlyArray<{ context: string; label: string; hint: string }> = [
  {
    context: 'auto_inject:communication_style',
    label: 'communication style',
    hint: '回答怎么框定（详略程度、口吻风格、是否使用列表等）'
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

// 长期记忆浏览与修正（DESIGN §8）：主动召回 / 自动注入两 tab。
export function MemorySection(): React.ReactElement {
  const tab = useStore($memoryBrowserTab)
  const { requestGateway } = useGatewayRequest()

  const [rows, setRows] = useState<MemoryRow[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [draftById, setDraftById] = useState<Record<number, string>>({})
  const [savingById, setSavingById] = useState<Record<number, boolean>>({})
  // 每次调用 ``load`` 时递增；``load`` 发起更新版本之后才返回的旧响应被丢弃，
  // 防止慢响应覆盖已切换到新 tab 的快响应。
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

  // 函数式 setState 无需镜像 ref 也能拿到上一次的 rows；
  // 回滚分支从点击时闭包捕获的 `rows` 快照里同时还原 rows[i].content 与 draftById[i]。
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
    if (next !== tab) {
      setMemoryBrowserTab(next)
    }
  }

  return (
    <section>
      <div className="mb-2 flex items-center gap-1.5">
        <button
          className={tab === 'recall' ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
          onClick={() => switchTab('recall')}
          type="button"
        >
          主动召回 · {counts?.recall ?? '…'}
        </button>
        <button
          className={tab === 'auto_inject' ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
          onClick={() => switchTab('auto_inject')}
          type="button"
        >
          自动注入 · {counts?.auto_inject ?? '…'}
        </button>
        <span className={cn(HINT_TEXT, 'ml-auto')}>{counts?.user_profile ?? '…'} 个 user_profile 由你独占</span>
      </div>

      {hint && <p className="mb-2 text-xs text-amber-300/90">{hint}</p>}

      {loading ? (
        <p className="text-xs text-muted">加载中…</p>
      ) : tab === 'recall' ? (
        rows.length === 0 ? (
          <p className="text-xs text-muted">还没有召回记忆。精灵会在对话中主动写下。</p>
        ) : (
          <div className="space-y-2.5">
            {rows.map(r => {
              const tags = parseTags(r.tags)
              const draft = draftById[r.id] ?? ''
              const dirty = draft !== (r.content ?? '')
              const saving = !!savingById[r.id]

              return (
                <div className="rounded-xl border border-line-hairline bg-surface-card p-3" key={r.id}>
                  <textarea
                    className={cn(INPUT_CLASS, 'resize-none')}
                    disabled={saving}
                    onChange={e => setDraftById(d => ({ ...d, [r.id]: e.target.value }))}
                    rows={3}
                    value={draft}
                  />
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {tags.map(t => (
                      <span className={CHIP} key={t}>
                        {t}
                      </span>
                    ))}
                  </div>
                  <p className={cn(HINT_TEXT, 'mt-1')}>
                    {r.context ?? '—'} · 更新 {r.updated_at ?? '—'} · {draft.length} chars
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      className={BTN_SUBTLE}
                      disabled={saving || !dirty}
                      onClick={() => void saveRecall(r.id)}
                      type="button"
                    >
                      {saving ? '保存中…' : '保存'}
                    </button>
                    <button className={BTN_GHOST} disabled={saving} onClick={() => void del(r.id)} type="button">
                      删除
                    </button>
                    {!dirty && <span className={cn(HINT_TEXT, 'ml-1')}>已保存</span>}
                  </div>
                </div>
              )
            })}
          </div>
        )
      ) : (
        <div className="space-y-2.5">
          <p className={HINT_TEXT}>
            自动注入段每次对话都默念一遍，LLM 写入时已限 {MAX_AUTO_INJECT_CONTENT_CHARS} 字符。这里你可以查看或修正。
          </p>
          {AUTO_INJECT_SLOTS.map(slot => {
            const row = rows.find(r => r.context === slot.context)
            const draft = row ? (draftById[row.id] ?? row.content ?? '') : ''
            const dirty = !!row && draft !== (row.content ?? '')
            const overLimit = draft.length > MAX_AUTO_INJECT_CONTENT_CHARS
            const saving = !!row && !!savingById[row.id]

            return (
              <div className="rounded-xl border border-line-hairline bg-surface-card p-3" key={slot.context}>
                <p className="text-[11px] font-medium text-strong">{slot.label}</p>
                <p className="mb-1.5 mt-0.5 text-[10px] text-muted">{slot.hint}</p>
                {row ? (
                  <>
                    <textarea
                      className={cn(INPUT_CLASS, 'resize-none')}
                      disabled={saving}
                      onChange={e => setDraftById(d => ({ ...d, [row.id]: e.target.value }))}
                      rows={2}
                      value={draft}
                    />
                    <p className={cn(HINT_TEXT, 'mt-1')}>
                      {draft.length} / {MAX_AUTO_INJECT_CONTENT_CHARS} chars · 更新 {row.updated_at ?? '—'}
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        className={BTN_SUBTLE}
                        disabled={saving || !dirty || overLimit}
                        onClick={() => void saveRecall(row.id)}
                        type="button"
                      >
                        {saving ? '保存中…' : '保存'}
                      </button>
                      <button className={BTN_GHOST} disabled={saving} onClick={() => void del(row.id)} type="button">
                        删除
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="text-[10px] text-faint">（空）让精灵在对话中自然填入</p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
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
