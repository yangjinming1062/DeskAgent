import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { $persona, assemblePersona, hydratePersona, PERSONALITY_PRESETS, RELATIONSHIP_PRESETS } from '@/companion'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, Chip, FIELD_LABEL, INPUT_CLASS, SECTION_TITLE, TechCard } from '@/shared/panel'

// 可编辑的 persona 字段：name / relationship / personality。
// 锁定的视觉锚点字段（species / gender / appearance）刻意不可编辑——见 docs/DESIGN.md §5.4。
export function PersonaSection(): React.JSX.Element {
  const persona = useStore($persona)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(persona?.name ?? '')
  const [relationship, setRelationship] = useState(persona?.relationship ?? '')
  const [personality, setPersonality] = useState(persona?.personality ?? '')
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<string | null>(null)

  const startEdit = (): void => {
    setName(persona?.name ?? '')
    setRelationship(persona?.relationship ?? '')
    setPersonality(persona?.personality ?? '')
    setHint(null)
    setEditing(true)
  }

  const save = async (): Promise<void> => {
    const trimmed = name.trim()

    if (!trimmed) {
      setHint('得给我起个名字呀')

      return
    }

    setSaving(true)
    setHint(null)

    // C2：PUT 与 hydrate 的失败模式分开——PUT 成功后即便 GET 短暂失败，
    // 也不能当成保存失败（诱导用户重试会造成重复写入）。
    // 把当前 persona 作为 previous 传入，让锁定的视觉锚点字段原样带回。
    let putOk = false

    try {
      await window.spiritagent.api({
        body: {
          definition_json: JSON.stringify(
            assemblePersona(
              {
                name: trimmed,
                personality: personality.trim(),
                relationship: relationship.trim()
              },
              persona ?? undefined
            )
          )
        },
        method: 'PUT',
        path: '/api/companion/persona'
      })
      putOk = true
    } catch {
      setHint('保存失败，稍后再试')
      setSaving(false)

      return
    }

    if (putOk) {
      const result = await hydratePersona({ silent: true })

      if (!result.ok) {
        // 后端已经有人设，本地副本没刷出来。给一条更温和的提示，
        // 让用户知道下次 hydrate 之前（下一次保存、重启等）看到的是旧值。
        setHint('已保存，但本地刷新失败，稍后再试')
      }
    }

    setEditing(false)
    setSaving(false)
  }

  if (!editing) {
    const tags = [persona?.relationship, persona?.personality].filter(Boolean)

    return (
      <section>
        <p className={cn(SECTION_TITLE, 'mb-1.5')}>角色</p>
        <TechCard className="p-4 space-y-2 border-line-standard" tilt>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-accent animate-pulse shadow-[0_0_6px_var(--ui-accent)]" />
              <p className="font-semibold text-sm text-strong">{persona?.name ?? '伙伴'}</p>
            </div>
            <span className="font-mono text-[9px] text-faint tracking-widest">[PERSONA.CORE]</span>
          </div>
          <p className="text-xs text-body">{tags.length ? tags.join(' · ') : '还没设定性格'}</p>
          {persona?.appearance && (
            <p className="text-[11px] text-muted bg-fill-faint p-2 rounded-lg border border-line-hairline font-mono">
              {persona.appearance}
            </p>
          )}
        </TechCard>
        <button className={cn(BTN_SUBTLE, 'mt-2.5')} onClick={startEdit} type="button">
          编辑角色
        </button>
      </section>
    )
  }

  return (
    <section>
      <p className={cn(SECTION_TITLE, 'mb-1.5')}>编辑角色</p>
      <div className="space-y-3">
        <label className="block">
          <span className={FIELD_LABEL}>名字</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setName(e.target.value)}
            placeholder="给我起个名字"
            value={name}
          />
        </label>
        <label className="block">
          <span className={FIELD_LABEL}>角色定位</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setRelationship(e.target.value)}
            placeholder="或者自由描述…"
            value={relationship}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {RELATIONSHIP_PRESETS.map(p => (
              <Chip active={relationship === p} key={p} label={p} onClick={() => setRelationship(p)} />
            ))}
          </div>
        </label>
        <label className="block">
          <span className={FIELD_LABEL}>性格</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setPersonality(e.target.value)}
            placeholder="自由描述…"
            value={personality}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {PERSONALITY_PRESETS.map(p => (
              <Chip active={personality === p} key={p} label={p} onClick={() => setPersonality(p)} />
            ))}
          </div>
        </label>
        {hint && <p className="text-[11px] text-amber-300/90">{hint}</p>}
        <div className="flex gap-2">
          <button
            className={cn(BTN_SUBTLE, 'flex-1')}
            disabled={saving}
            onClick={() => setEditing(false)}
            type="button"
          >
            取消
          </button>
          <button className={cn(BTN_PRIMARY, 'flex-1')} disabled={saving} onClick={() => void save()} type="button">
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </section>
  )
}
