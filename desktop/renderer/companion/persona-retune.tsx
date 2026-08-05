import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { clearClipCatalog } from '@/companion/clip-store'
import { assemblePersona, MAX_APPEARANCE } from '@/companion/persona'
import { hydratePersona } from '@/companion/persona-store'

interface PersonaRetuneProps {
  initial: {
    name: string
    personality: string
    speaking_style: string
    biological_type: string
    gender: string
    appearance: string
    background: string
    user_call_name: string
    user_gender: string
    user_age_bucket: string
    user_hobbies: string
    user_freeform: string
  }
  onClose: () => void
  onSaved: () => void
}

// Speaking-style chips that the user can explicitly pick. Each chip maps to
// the same string the backend stores; the wizard passes the chip verbatim so
// the user's choice is preserved end-to-end (overriding the persona-key
// derivation in ``assemblePersona``). The free-text option below also
// accepts arbitrary strings, with the same null-on-empty contract.
const SPEAKING_STYLE_PRESETS = ['温柔亲切', '俏皮带点小傲娇', '沉稳简洁', '轻快活泼', '专业干练']
const PERSONALITY_PRESETS = ['温柔体贴', '活泼好动', '冷静理性', '毒舌傲娇']
const SPECIES_PRESETS = ['人类', '灵兽', '精灵', '机甲', '幻形']
const CHARACTER_GENDER_PRESETS = ['女', '男', '其他', '不指定']
const APPEARANCE_PRESETS = ['优雅古典', '现代利落', '萌系可爱', '冷酷暗黑']
const ROLE_PRESETS = ['爱人', '秘书', '专属管家', '无话不谈的朋友']

const inputClass =
  'w-full rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white placeholder:text-white/30 outline-none focus:border-white/40'

const presetClass =
  'rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[11px] text-white/70 transition hover:bg-white/15'

// Field schema: each step owns a list of fields. ``set`` is the setter
// for the corresponding local state slice; ``max`` truncates long inputs
// (only used by appearance). ``presets`` is optional; when present,
// chips appear under the input. ``placeholder`` only matters when the
// field is empty at mount (drives e.g. the speaking_style hint).
type FieldSchema = {
  key: keyof typeof EMPTY
  label: string
  set: (v: string) => void
  presets?: readonly string[]
  max?: number
  placeholder?: string
  multiline?: boolean
}

const EMPTY = {
  name: '',
  characterGender: '',
  species: '',
  background: '',
  personality: '',
  speakingStyle: '',
  appearance: '',
  userCallName: '',
  userGender: '',
  userAgeBucket: '',
  userHobbies: '',
  userFreeform: ''
} as const

const STEPS: { title: string; fields: FieldSchema[] }[] = [
  {
    title: '名字 与 形象性别',
    fields: [
      { key: 'name', label: '名字', set: () => {} },
      {
        key: 'characterGender',
        label: '形象性别',
        set: () => {},
        max: 64,
        presets: CHARACTER_GENDER_PRESETS
      }
    ]
  },
  {
    title: '物种 与 关系',
    fields: [
      { key: 'species', label: '物种', set: () => {}, max: 64, presets: SPECIES_PRESETS },
      { key: 'background', label: '关系 / 角色定位', set: () => {}, presets: ROLE_PRESETS }
    ]
  },
  {
    title: '性格 与 语气（修复 speaking_style 被静默覆盖的坑）',
    fields: [
      { key: 'personality', label: '性格', set: () => {}, presets: PERSONALITY_PRESETS },
      {
        key: 'speakingStyle',
        label: '说话风格（显式可选）',
        set: () => {},
        placeholder: '留空将根据性格自动派生',
        presets: [...SPEAKING_STYLE_PRESETS, '']
      }
    ]
  },
  {
    title: '形象描述',
    fields: [
      {
        key: 'appearance',
        label: '形象',
        set: () => {},
        max: MAX_APPEARANCE,
        presets: APPEARANCE_PRESETS,
        multiline: true
      }
    ]
  },
  {
    title: '让伙伴更了解你',
    fields: [
      { key: 'userCallName', label: '希望被怎么称呼', set: () => {} },
      { key: 'userGender', label: '你的性别', set: () => {} },
      { key: 'userAgeBucket', label: '年龄段', set: () => {} },
      { key: 'userHobbies', label: '爱好', set: () => {} },
      { key: 'userFreeform', label: '还有什么想告诉我', set: () => {}, multiline: true }
    ]
  }
]

// Review step is data-driven too: a single Row per state slice, labeled.
const REVIEW_ROWS: { key: keyof typeof EMPTY; label: string; fallback?: string }[] = [
  { key: 'name', label: '名字' },
  { key: 'characterGender', label: '形象性别' },
  { key: 'species', label: '物种' },
  { key: 'background', label: '关系' },
  { key: 'personality', label: '性格' },
  { key: 'speakingStyle', label: '说话风格', fallback: '自动派生' },
  { key: 'appearance', label: '形象' },
  { key: 'userCallName', label: '称呼' },
  { key: 'userGender', label: '我的性别' },
  { key: 'userAgeBucket', label: '年龄段' },
  { key: 'userHobbies', label: '爱好' },
  { key: 'userFreeform', label: '补充' }
]

export function PersonaRetune({ initial, onClose, onSaved }: PersonaRetuneProps): React.ReactElement {
  const { requestGateway } = useGatewayRequest()
  const [step, setStep] = useState<number>(0)
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  // Tracks whether the wizard is still mounted. Closing the modal mid-save
  // unmounts the component; the in-flight ``save()`` continues to run,
  // and the resulting ``window.confirm`` would appear as an orphan
  // dialog with no visible source. Suppress confirm + onSaved when the
  // user already dismissed the wizard.
  const mountedRef = useRef(true)
  useEffect(
    () => () => {
      mountedRef.current = false
    },
    []
  )

  const [name, setName] = useState(initial.name)
  const [characterGender, setCharacterGender] = useState(initial.gender)
  const [species, setSpecies] = useState(initial.biological_type)
  // Mirror the wire field name (``background``) directly so the rename
  // round-trip through ``assemblePersona({role, ...})`` is not load-bearing
  // for correctness — only for backwards compat with the existing schema.
  const [background, setBackground] = useState(initial.background)
  const [personality, setPersonality] = useState(initial.personality)
  const [speakingStyle, setSpeakingStyle] = useState(initial.speaking_style)
  const [appearance, setAppearance] = useState(initial.appearance)
  const [userCallName, setUserCallName] = useState(initial.user_call_name)
  const [userGender, setUserGender] = useState(initial.user_gender)
  const [userAgeBucket, setUserAgeBucket] = useState(initial.user_age_bucket)
  const [userHobbies, setUserHobbies] = useState(initial.user_hobbies)
  const [userFreeform, setUserFreeform] = useState(initial.user_freeform)

  // Setter map keyed by field ``key``. Avoids a switch/case per step.
  const setters: Record<keyof typeof EMPTY, (v: string) => void> = {
    name: setName,
    characterGender: setCharacterGender,
    species: setSpecies,
    background: setBackground,
    personality: setPersonality,
    speakingStyle: setSpeakingStyle,
    appearance: setAppearance,
    userCallName: setUserCallName,
    userGender: setUserGender,
    userAgeBucket: setUserAgeBucket,
    userHobbies: setUserHobbies,
    userFreeform: setUserFreeform
  }

  const values: Record<keyof typeof EMPTY, string> = {
    name,
    characterGender,
    species,
    background,
    personality,
    speakingStyle,
    appearance,
    userCallName,
    userGender,
    userAgeBucket,
    userHobbies,
    userFreeform
  }

  const totalSteps = STEPS.length + 1
  const isReview = step === STEPS.length

  const next = () => setStep(s => Math.min(s + 1, totalSteps - 1))
  const prev = () => setStep(s => Math.max(s - 1, 0))

  const save = async () => {
    const trimmed = name.trim()

    if (!trimmed) {
      setHint('得给我起个名字呀')
      setStep(0)

      return
    }

    setSaving(true)
    setHint(null)

    try {
      await window.deskagent.api({
        body: assemblePersona({
          name: trimmed,
          personality,
          speaking_style: speakingStyle,
          species,
          character_gender: characterGender,
          appearance: appearance.slice(0, MAX_APPEARANCE),
          role: background,
          user_call_name: userCallName,
          user_gender: userGender,
          user_age_bucket: userAgeBucket,
          user_hobbies: userHobbies,
          user_freeform: userFreeform
        }),
        method: 'PUT',
        path: '/api/companion/persona'
      })
      await hydratePersona()

      if (!mountedRef.current) {
        return
      }

      onSaved()

      if (window.confirm('角色更新啦，要重新生成我的形象吗？')) {
        try {
          const res = (await requestGateway('avatar.regenerate', {})) as { queued?: boolean } | undefined

          if (res?.queued) {
            clearClipCatalog()
          }
        } catch {
          /* generation failed or rejected — silent */
        }
      }

      onClose()
    } catch {
      setHint('保存失败了，稍后再试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      style={{ pointerEvents: 'auto' }}
    >
      <div className="flex max-h-[80vh] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-white/15 bg-black/80 text-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h2 className="text-sm font-semibold">重新对话微调性格</h2>
          <button
            aria-label="关闭"
            className="text-white/50 transition hover:text-white"
            onClick={onClose}
            type="button"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4 text-xs">
          {hint && <p className="mb-2 text-amber-300/80">{hint}</p>}

          {!isReview && (
            <div className="space-y-2.5">
              <p className="text-[11px] text-white/60">
                第 {step + 1} 步 · {STEPS[step].title}
              </p>
              {STEPS[step].fields.map(field => (
                <Field
                  field={field}
                  key={field.key}
                  onChange={setters[field.key]}
                  placeholder={field.placeholder}
                  value={values[field.key]}
                />
              ))}
            </div>
          )}

          {isReview && (
            <div className="space-y-2">
              <p className="text-[11px] text-white/60">第 {step + 1} 步 · 回顾</p>
              <dl className="space-y-1 rounded-lg border border-white/10 bg-white/5 p-3 text-[11px]">
                {REVIEW_ROWS.map(row => (
                  <Row key={row.key} label={row.label} value={values[row.key] || row.fallback || ''} />
                ))}
              </dl>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 border-t border-white/10 px-4 py-3">
          <button
            className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-[11px] text-white/70 transition hover:bg-white/15 disabled:opacity-40"
            disabled={step === 0 || saving}
            onClick={prev}
            type="button"
          >
            上一步
          </button>
          <span className="ml-auto text-[10px] text-white/40">
            {step + 1} / {totalSteps}
          </span>
          {!isReview ? (
            <button
              className="rounded-lg border border-white/40 bg-white/15 px-3 py-1.5 text-[11px] font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
              disabled={saving}
              onClick={next}
              type="button"
            >
              下一步
            </button>
          ) : (
            <button
              className="rounded-lg border border-white/40 bg-white/15 px-3 py-1.5 text-[11px] font-medium text-white transition hover:bg-white/25 disabled:opacity-40"
              disabled={saving}
              onClick={() => void save()}
              type="button"
            >
              {saving ? '保存中…' : '保存'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

interface FieldProps {
  field: FieldSchema
  value: string
  onChange: (v: string) => void
  placeholder?: string
}

function Field({ field, value, onChange, placeholder }: FieldProps): React.ReactElement {
  // Last entry of the presets list, when an empty string, is the
  // "auto-derive / clear" affordance — only meaningful for speaking_style.
  const isClearPreset = field.presets && field.presets[field.presets.length - 1] === ''
  const max = field.max ?? Infinity
  const handleChange = (v: string) => onChange(max === Infinity ? v : v.slice(0, max))

  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-white/50">{field.label}</span>
      {field.multiline ? (
        <textarea
          className={`${inputClass} resize-none`}
          onChange={e => handleChange(e.target.value)}
          placeholder={placeholder}
          rows={field.key === 'appearance' ? 3 : 2}
          value={value}
        />
      ) : (
        <input
          className={inputClass}
          onChange={e => handleChange(e.target.value)}
          placeholder={placeholder}
          value={value}
        />
      )}
      {field.presets && field.presets.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {field.presets.map(p => {
            const isClear = p === '' && isClearPreset
            const active = isClear ? value === '' : value === p
            const labelText = isClear ? '自动派生' : p

            return (
              <button
                className={`${presetClass} ${active ? 'border-white/40 bg-white/20 text-white' : ''}`}
                key={p || 'clear'}
                onClick={() => handleChange(p)}
                type="button"
              >
                {labelText}
              </button>
            )
          })}
        </div>
      )}
      {field.key === 'appearance' && (
        <span className="mt-1 block text-[10px] text-white/40">
          {value.length} / {MAX_APPEARANCE}
        </span>
      )}
    </label>
  )
}

function Row({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-white/50">{label}</dt>
      <dd className="flex-1 text-white/90">{value || '—'}</dd>
    </div>
  )
}
