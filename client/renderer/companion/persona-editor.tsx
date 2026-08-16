import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { PERSONA_INPUT_CLASS, PERSONA_PRESET_CLASS } from '@/companion/input-class'
import { assemblePersona } from '@/companion/persona'
import { PERSONALITY_PRESETS, ROLE_PRESETS } from '@/companion/persona-presets'
import { $persona, hydratePersona } from '@/companion/persona-store'

const inputClass = PERSONA_INPUT_CLASS
const presetClass = PERSONA_PRESET_CLASS

// Editable persona fields: name / role / personality.
// appearance_outfit is read-only (maintained by the wardrobe system).
// Locked visual-anchor fields (species / gender / appearance_core) are
// intentionally not editable — see DESIGN.md §5.4.
export function PersonaSection(): React.JSX.Element {
  const persona = useStore($persona)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(persona?.name ?? '')
  const [role, setRole] = useState(persona?.background ?? '')
  const [personality, setPersonality] = useState(persona?.personality ?? '')
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<string | null>(null)

  const startEdit = () => {
    setName(persona?.name ?? '')
    setRole(persona?.background ?? '')
    setPersonality(persona?.personality ?? '')
    setHint(null)
    setEditing(true)
  }

  const save = async () => {
    const trimmed = name.trim()

    if (!trimmed) {
      setHint('得给我起个名字呀')

      return
    }

    setSaving(true)
    setHint(null)

    // C2: separate the PUT (write) and hydrate (read) failure modes. A
    // transient GET failure after a successful PUT must NOT look like a
    // save failure — the backend has the data; surfacing "保存失败"
    // would tempt the user to retry and double-write.
    //
    // Current persona passed as `previous` so locked visual-anchor fields
    // are re-included verbatim; see DESIGN.md §5.4.
    let putOk = false

    try {
      await window.spiritagent.api({
        body: {
          definition_json: JSON.stringify(
            assemblePersona(
              {
                name: trimmed,
                personality,
                role
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
      setHint('保存失败了，稍后再试')
      setSaving(false)

      return
    }

    if (putOk) {
      const result = await hydratePersona({ silent: true })

      if (!result.ok) {
        // Backend has the persona; the local copy didn't refresh. Show a
        // softer hint so the user knows to expect a stale view until the
        // next hydrate (next save, restart, etc.).
        setHint('已保存，但本地刷新失败，稍后再试')
      }
    }

    setEditing(false)
    setSaving(false)
  }

  if (!editing) {
    const tags = [persona?.background, persona?.personality].filter(Boolean)

    return (
      <div>
        <p className="mb-1.5 text-xs font-medium text-white/80">角色</p>
        <div className="space-y-0.5 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs">
          <p className="font-medium text-white">{persona?.name ?? '伙伴'}</p>
          <p className="text-white/50">{tags.length ? tags.join(' · ') : '还没设定性格'}</p>
          {persona?.appearance_outfit && <p className="text-white/40">{persona.appearance_outfit}</p>}
        </div>
        <button
          className="mt-1.5 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/80 transition hover:bg-white/15"
          onClick={startEdit}
          type="button"
        >
          编辑角色
        </button>
        <p className="mt-1.5 text-[10px] text-white/30">修改我的名字、定位与性格</p>
      </div>
    )
  }

  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-white/80">编辑角色</p>
      <div className="space-y-2.5">
        <label className="block">
          <span className="mb-1 block text-[11px] text-white/50">名字</span>
          <input
            className={inputClass}
            onChange={e => setName(e.target.value)}
            placeholder="给我起个名字"
            value={name}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-white/50">角色定位</span>
          <input
            className={inputClass}
            onChange={e => setRole(e.target.value)}
            placeholder="或者自由描述…"
            value={role}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {ROLE_PRESETS.map(p => (
              <button className={presetClass} key={p} onClick={() => setRole(p)} type="button">
                {p}
              </button>
            ))}
          </div>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-white/50">性格</span>
          <input
            className={inputClass}
            onChange={e => setPersonality(e.target.value)}
            placeholder="自由描述…"
            value={personality}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {PERSONALITY_PRESETS.map(p => (
              <button className={presetClass} key={p} onClick={() => setPersonality(p)} type="button">
                {p}
              </button>
            ))}
          </div>
        </label>
        {persona?.appearance_outfit && (
          <div>
            <span className="mb-1 block text-[11px] text-white/50">当前着装</span>
            <p className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-white/60">
              {persona.appearance_outfit}
            </p>
            <span className="mt-1 block text-[10px] text-white/30">着装由换装系统维护，请在换装设计面板中更换</span>
          </div>
        )}
        {hint && <p className="text-[11px] text-amber-300/80">{hint}</p>}
        <div className="flex gap-2 pt-1">
          <button
            className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/70 transition hover:bg-white/15"
            disabled={saving}
            onClick={() => setEditing(false)}
            type="button"
          >
            取消
          </button>
          <button
            className="flex-1 rounded-lg border border-white/40 bg-white/15 px-3 py-2 text-xs font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
            disabled={saving}
            onClick={() => void save()}
            type="button"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
