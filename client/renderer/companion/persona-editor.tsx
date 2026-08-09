import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { PERSONA_INPUT_CLASS, PERSONA_PRESET_CLASS } from '@/companion/input-class'
import { assemblePersona, MAX_APPEARANCE } from '@/companion/persona'
import {
  APPEARANCE_PRESETS,
  CHARACTER_GENDER_PRESETS,
  PERSONALITY_PRESETS,
  ROLE_PRESETS,
  SPECIES_PRESETS
} from '@/companion/persona-presets'
import { $persona, hydratePersona } from '@/companion/persona-store'
import { TWO_STEP_AVATAR_PROMPT, TWO_STEP_FULLBODY_PROMPT } from '@/companion/portrait-flow-copy'
import { $activeAvatarId, setRegenFeedback } from '@/companion/portrait-store'
import { useRegeneratePortrait } from '@/companion/use-regenerate-portrait'

const inputClass = PERSONA_INPUT_CLASS
const presetClass = PERSONA_PRESET_CLASS

// Runtime persona editor: revisits the onboarding fields prefilled with the
// current persona, PUTs the assembled payload (now including species /
// character_gender / appearance in addition to name / role / personality),
// then re-hydrates $persona. Avatar regeneration is offered (not forced)
// since a persona change should re-seed the portrait but regeneration is
// costly. user_* fields are intentionally not edited here — by arch §7.6 they
// live in the memory layer and any edits go through memory_retain /
// memory_forget tools.
export function PersonaSection(): React.JSX.Element {
  const persona = useStore($persona)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(persona?.name ?? '')
  const [role, setRole] = useState(persona?.background ?? '')
  const [personality, setPersonality] = useState(persona?.personality ?? '')
  const [species, setSpecies] = useState(persona?.biological_type ?? '')
  const [characterGender, setCharacterGender] = useState(persona?.gender ?? '')
  const [appearance, setAppearance] = useState(persona?.appearance ?? '')
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const [showPostSave, setShowPostSave] = useState(false)
  // Two-step portrait regen after persona save — same flow as onboarding /
  // settings. Avatar id flows from the global atom; we only own the local
  // postSaveStep so the inline panel can swap its two CTAs.
  const [postSaveStep, setPostSaveStep] = useState<'avatar' | 'fullbody' | null>(null)
  const activeAvatarId = useStore($activeAvatarId)

  const { regenerate: regenerateAvatar, hint: avatarHint, busy: avatarBusy } = useRegeneratePortrait({ step: 'avatar' })

  const {
    regenerate: regenerateFullbody,
    hint: fullbodyHint,
    busy: fullbodyBusy
  } = useRegeneratePortrait({ step: 'fullbody', avatarId: activeAvatarId })

  const startEdit = () => {
    setName(persona?.name ?? '')
    setRole(persona?.background ?? '')
    setPersonality(persona?.personality ?? '')
    setSpecies(persona?.biological_type ?? '')
    setCharacterGender(persona?.gender ?? '')
    setAppearance(persona?.appearance ?? '')
    setHint(null)
    setShowPostSave(false)
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
    let putOk = false

    try {
      await window.deskagent.api({
        body: {
          definition_json: JSON.stringify(
            assemblePersona({
              name: trimmed,
              personality,
              role,
              species,
              character_gender: characterGender,
              appearance: appearance.slice(0, MAX_APPEARANCE)
            })
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
    setShowPostSave(true)
    setPostSaveStep('avatar')
    setSaving(false)
  }

  if (!editing) {
    const tags = [persona?.biological_type, persona?.gender, persona?.background, persona?.personality].filter(Boolean)

    return (
      <div>
        <p className="mb-1.5 text-xs font-medium text-white/80">角色</p>
        <div className="space-y-0.5 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs">
          <p className="font-medium text-white">{persona?.name ?? '伙伴'}</p>
          <p className="text-white/50">{tags.length ? tags.join(' · ') : '还没设定性格'}</p>
          {persona?.appearance && <p className="text-white/40">{persona.appearance}</p>}
        </div>
        {showPostSave && (
          <div className="mt-2 space-y-2 rounded-lg border border-white/15 bg-white/5 px-3 py-2">
            <p className="text-xs text-white/80">
              {postSaveStep === 'avatar' ? TWO_STEP_AVATAR_PROMPT : TWO_STEP_FULLBODY_PROMPT}
            </p>
            {avatarHint && <p className="text-[11px] text-amber-300/80">{avatarHint}</p>}
            {fullbodyHint && <p className="text-[11px] text-amber-300/80">{fullbodyHint}</p>}
            {postSaveStep === 'avatar' ? (
              <div className="flex gap-2">
                <button
                  className="flex-1 rounded-lg border border-white/40 bg-white/15 px-3 py-1.5 text-[11px] font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
                  disabled={avatarBusy}
                  onClick={async () => {
                    setRegenFeedback(appearance.slice(0, MAX_APPEARANCE))
                    await regenerateAvatar()
                    setPostSaveStep('fullbody')
                  }}
                  type="button"
                >
                  {avatarBusy ? '生成中…' : '重新生成头像'}
                </button>
                <button
                  className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-[11px] text-white/70 transition hover:bg-white/15 disabled:opacity-40"
                  disabled={avatarBusy}
                  onClick={() => setShowPostSave(false)}
                  type="button"
                >
                  暂后
                </button>
              </div>
            ) : (
              <div className="flex gap-2">
                <button
                  className="flex-1 rounded-lg border border-white/40 bg-white/15 px-3 py-1.5 text-[11px] font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
                  disabled={fullbodyBusy}
                  onClick={async () => {
                    await regenerateFullbody()
                    setShowPostSave(false)
                  }}
                  type="button"
                >
                  {fullbodyBusy ? '生成中…' : '生成全身图'}
                </button>
                <button
                  className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-[11px] text-white/70 transition hover:bg-white/15 disabled:opacity-40"
                  disabled={fullbodyBusy}
                  onClick={() => setShowPostSave(false)}
                  type="button"
                >
                  暂后
                </button>
              </div>
            )}
          </div>
        )}
        <button
          className="mt-1.5 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/80 transition hover:bg-white/15"
          onClick={startEdit}
          type="button"
        >
          编辑角色
        </button>
        <p className="mt-1.5 text-[10px] text-white/30">修改我的名字、形象、定位与性格</p>
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
          <span className="mb-1 block text-[11px] text-white/50">生物类型</span>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {SPECIES_PRESETS.map(p => (
              <button
                className={`${presetClass} ${species === p ? 'border-white/40 bg-white/20 text-white' : ''}`}
                key={p}
                onClick={() => setSpecies(p)}
                type="button"
              >
                {p}
              </button>
            ))}
          </div>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-white/50">角色性别</span>
          <input
            className={inputClass}
            onChange={e => setCharacterGender(e.target.value.slice(0, 64))}
            placeholder="比如：女、男…"
            value={characterGender}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {CHARACTER_GENDER_PRESETS.map(p => (
              <button className={presetClass} key={p} onClick={() => setCharacterGender(p)} type="button">
                {p}
              </button>
            ))}
          </div>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-white/50">形象描述</span>
          <textarea
            className={`${inputClass} resize-none`}
            onChange={e => setAppearance(e.target.value.slice(0, MAX_APPEARANCE))}
            placeholder="比如：金发绿眼、黑色礼帽…"
            rows={3}
            value={appearance}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {APPEARANCE_PRESETS.map(p => (
              <button className={presetClass} key={p} onClick={() => setAppearance(p)} type="button">
                {p}
              </button>
            ))}
          </div>
          <span className="mt-1 block text-[10px] text-white/40">
            {appearance.length} / {MAX_APPEARANCE}
          </span>
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
