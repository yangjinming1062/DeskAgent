import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { PERSONA_INPUT_CLASS, PERSONA_PRESET_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { assemblePersona } from '@/companion/persona'
import {
  PERSONALITY_PRESETS,
  type PersonalityPreset,
  ROLE_PRESETS,
  type RolePreset,
  SPEAKING_STYLE_PRESETS,
  type SpeakingStylePreset
} from '@/companion/persona-presets'
import { $persona, hydratePersona } from '@/companion/persona-store'

interface PersonaRetuneProps {
  initial: {
    name: string
    personality: string
    speaking_style: string
    background: string
    user_call_name: string
    user_gender: string
    user_age_bucket: string
    user_hobbies: string
    user_freeform: string
  }
  onClose: () => void
}

const inputClass = PERSONA_INPUT_CLASS
const presetClass = PERSONA_PRESET_CLASS

// 字段 schema：每一步持有一组字段。``presets`` 的类型是全部已知 preset token 的联合再加 ''
// （speakingStyle 用的「自动派生」标记）。这样 STEPS 里写成「喜爱」这种拼写错误会编译失败，
// 而不是默默渲染出一个空 chip。
// species / character_gender / appearance 在这里不可编辑。
type PresetValue = PersonalityPreset | RolePreset | SpeakingStylePreset | ''

type PersonaFieldKey =
  | 'background'
  | 'name'
  | 'personality'
  | 'speakingStyle'
  | 'userAgeBucket'
  | 'userCallName'
  | 'userFreeform'
  | 'userGender'
  | 'userHobbies'

type FieldSchema = {
  key: PersonaFieldKey
  label: string
  presets?: readonly PresetValue[]
  max?: number
  placeholder?: string
  multiline?: boolean
}

const STEPS: { title: string; fields: FieldSchema[] }[] = [
  {
    title: '角色定义：名称',
    fields: [{ key: 'name', label: '角色名', max: 64, placeholder: '给你起个名字' }]
  },
  {
    title: '关系 与 性格',
    fields: [
      { key: 'background', label: '关系 / 角色定位', presets: ROLE_PRESETS },
      { key: 'personality', label: '性格', presets: PERSONALITY_PRESETS },
      {
        key: 'speakingStyle',
        label: '说话风格（显式可选）',
        placeholder: '留空将根据性格自动派生',
        presets: [...SPEAKING_STYLE_PRESETS, '']
      }
    ]
  },
  {
    title: '让伙伴更了解你',
    fields: [
      { key: 'userCallName', label: '希望被怎么称呼' },
      { key: 'userGender', label: '你的性别' },
      { key: 'userAgeBucket', label: '年龄段' },
      { key: 'userHobbies', label: '爱好' },
      { key: 'userFreeform', label: '还有什么想告诉我', multiline: true }
    ]
  }
]

// 回顾步骤也是数据驱动的：每条状态切片对应一个 Row，带标签。
// 锁定的视觉锚点字段（species / gender / appearance）在这里不可编辑，
// 可以在设置里的角色区查看。
const REVIEW_ROWS: { fallback?: string; key: PersonaFieldKey; label: string }[] = [
  { key: 'name', label: '名字' },
  { key: 'background', label: '关系' },
  { key: 'personality', label: '性格' },
  { fallback: '自动派生', key: 'speakingStyle', label: '说话风格' },
  { key: 'userCallName', label: '称呼' },
  { key: 'userGender', label: '我的性别' },
  { key: 'userAgeBucket', label: '年龄段' },
  { key: 'userHobbies', label: '爱好' },
  { key: 'userFreeform', label: '补充' }
]

export function PersonaRetune({ initial, onClose }: PersonaRetuneProps): React.ReactElement {
  const persona = useStore($persona)
  const [step, setStep] = useState<number>(0)
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<null | string>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion('persona-retune', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  // 跟踪向导是否还挂载。保存中途关闭模态框会卸载组件；进行中的 ``save()`` 仍会跑完。
  const mountedRef = useRef(true)
  useEffect(
    () => () => {
      mountedRef.current = false
    },
    []
  )

  const [name, setName] = useState(initial.name)
  const [background, setBackground] = useState(initial.background)
  const [personality, setPersonality] = useState(initial.personality)
  const [speakingStyle, setSpeakingStyle] = useState(initial.speaking_style)
  const [userCallName, setUserCallName] = useState(initial.user_call_name)
  const [userGender, setUserGender] = useState(initial.user_gender)
  const [userAgeBucket, setUserAgeBucket] = useState(initial.user_age_bucket)
  const [userHobbies, setUserHobbies] = useState(initial.user_hobbies)
  const [userFreeform, setUserFreeform] = useState(initial.user_freeform)

  // 以字段 ``key`` 为键的 setter 映射。避免每一步写 switch/case。
  const setters: Record<PersonaFieldKey, (v: string) => void> = {
    background: setBackground,
    name: setName,
    personality: setPersonality,
    speakingStyle: setSpeakingStyle,
    userAgeBucket: setUserAgeBucket,
    userCallName: setUserCallName,
    userFreeform: setUserFreeform,
    userGender: setUserGender,
    userHobbies: setUserHobbies
  }

  const values: Record<PersonaFieldKey, string> = {
    background,
    name,
    personality,
    speakingStyle,
    userAgeBucket,
    userCallName,
    userFreeform,
    userGender,
    userHobbies
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

    // C2：把 PUT（写）与 hydrate（读）的失败模式分开——与 persona-editor 同理。
    // PUT 成功之后即便 GET 短暂失败，也不能被当成保存失败。
    //
    // 把当前 persona 作为 `previous` 传入，让锁定的视觉锚点字段原样带回——见 DESIGN.md §5.4。
    let putOk = false

    try {
      await window.spiritagent.api({
        body: {
          definition_json: JSON.stringify(
            assemblePersona(
              {
                name: trimmed,
                personality,
                speaking_style: speakingStyle,
                role: background,
                user_call_name: userCallName,
                user_gender: userGender,
                user_age_bucket: userAgeBucket,
                user_hobbies: userHobbies,
                user_freeform: userFreeform
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
      if (mountedRef.current) {
        setHint('保存失败了，稍后再试')
        setSaving(false)
      }

      return
    }

    if (!putOk) {
      return
    }

    const result = await hydratePersona({ silent: true })

    if (!mountedRef.current) {
      return
    }

    if (!result.ok) {
      // 后端已经有人设，本地副本没刷出来。给一条更温和的提示，
      // 让用户知道下次 hydrate 之前（下一次保存、重启等）看到的是旧值。
      setHint('已保存，但本地刷新失败，稍后再试')
      setSaving(false)

      return
    }

    setSaving(false)
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      ref={overlayRef}
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
                <Field field={field} key={field.key} onChange={setters[field.key]} value={values[field.key]} />
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
}

function Field({ field, value, onChange }: FieldProps): React.ReactElement {
  // presets 列表的最后一项如果是空字符串，表示「自动派生 / 清空」选项——
  // 只有 speaking_style 上才有意义。
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
          placeholder={field.placeholder}
          rows={2}
          value={value}
        />
      ) : (
        <input
          className={inputClass}
          onChange={e => handleChange(e.target.value)}
          placeholder={field.placeholder}
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
