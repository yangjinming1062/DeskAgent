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

const STEPS = [
  'name + gender',
  'species + role',
  'personality + speaking_style',
  'appearance',
  'user_*',
  'review'
] as const

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

  const next = () => setStep(s => Math.min(s + 1, STEPS.length - 1))
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
        // User dismissed the wizard during the PUT; skip the post-save
        // UI (confirm + onSaved) which would otherwise appear with no
        // visible source. onClose already ran on the dismiss.
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

          {step === 0 && (
            <div className="space-y-2.5">
              <p className="text-[11px] text-white/60">第 1 步 · 名字 与 形象性别</p>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">名字</span>
                <input className={inputClass} onChange={e => setName(e.target.value)} value={name} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">形象性别</span>
                <input
                  className={inputClass}
                  onChange={e => setCharacterGender(e.target.value.slice(0, 64))}
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
            </div>
          )}

          {step === 1 && (
            <div className="space-y-2.5">
              <p className="text-[11px] text-white/60">第 2 步 · 物种 与 关系</p>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">物种</span>
                <input className={inputClass} onChange={e => setSpecies(e.target.value.slice(0, 64))} value={species} />
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {SPECIES_PRESETS.map(p => (
                    <button className={presetClass} key={p} onClick={() => setSpecies(p)} type="button">
                      {p}
                    </button>
                  ))}
                </div>
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">关系 / 角色定位</span>
                <input className={inputClass} onChange={e => setBackground(e.target.value)} value={background} />
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {ROLE_PRESETS.map(p => (
                    <button className={presetClass} key={p} onClick={() => setBackground(p)} type="button">
                      {p}
                    </button>
                  ))}
                </div>
              </label>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-2.5">
              <p className="text-[11px] text-white/60">第 3 步 · 性格 与 语气（修复 speaking_style 被静默覆盖的坑）</p>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">性格</span>
                <input className={inputClass} onChange={e => setPersonality(e.target.value)} value={personality} />
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {PERSONALITY_PRESETS.map(p => (
                    <button className={presetClass} key={p} onClick={() => setPersonality(p)} type="button">
                      {p}
                    </button>
                  ))}
                </div>
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">说话风格（显式可选）</span>
                <input
                  className={inputClass}
                  onChange={e => setSpeakingStyle(e.target.value)}
                  placeholder="留空将根据性格自动派生"
                  value={speakingStyle}
                />
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {SPEAKING_STYLE_PRESETS.map(s => (
                    <button
                      className={`${presetClass} ${speakingStyle === s ? 'border-white/40 bg-white/20 text-white' : ''}`}
                      key={s}
                      onClick={() => setSpeakingStyle(s)}
                      type="button"
                    >
                      {s}
                    </button>
                  ))}
                  <button
                    className={`${presetClass} ${speakingStyle === '' ? 'border-white/40 bg-white/20 text-white' : ''}`}
                    onClick={() => setSpeakingStyle('')}
                    type="button"
                  >
                    自动派生
                  </button>
                </div>
              </label>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-2.5">
              <p className="text-[11px] text-white/60">第 4 步 · 形象描述</p>
              <label className="block">
                <textarea
                  className={`${inputClass} resize-none`}
                  onChange={e => setAppearance(e.target.value.slice(0, MAX_APPEARANCE))}
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
            </div>
          )}

          {step === 4 && (
            <div className="space-y-2.5">
              <p className="text-[11px] text-white/60">第 5 步 · 让伙伴更了解你</p>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">希望被怎么称呼</span>
                <input className={inputClass} onChange={e => setUserCallName(e.target.value)} value={userCallName} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">你的性别</span>
                <input className={inputClass} onChange={e => setUserGender(e.target.value)} value={userGender} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">年龄段</span>
                <input className={inputClass} onChange={e => setUserAgeBucket(e.target.value)} value={userAgeBucket} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">爱好</span>
                <input className={inputClass} onChange={e => setUserHobbies(e.target.value)} value={userHobbies} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-white/50">还有什么想告诉我</span>
                <textarea
                  className={`${inputClass} resize-none`}
                  onChange={e => setUserFreeform(e.target.value)}
                  rows={2}
                  value={userFreeform}
                />
              </label>
            </div>
          )}

          {step === 5 && (
            <div className="space-y-2">
              <p className="text-[11px] text-white/60">第 6 步 · 回顾</p>
              <dl className="space-y-1 rounded-lg border border-white/10 bg-white/5 p-3 text-[11px]">
                <Row label="名字" value={name} />
                <Row label="形象性别" value={characterGender} />
                <Row label="物种" value={species} />
                <Row label="关系" value={background} />
                <Row label="性格" value={personality} />
                <Row label="说话风格" value={speakingStyle || '自动派生'} />
                <Row label="形象" value={appearance} />
                <Row label="称呼" value={userCallName} />
                <Row label="我的性别" value={userGender} />
                <Row label="年龄段" value={userAgeBucket} />
                <Row label="爱好" value={userHobbies} />
                <Row label="补充" value={userFreeform} />
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
            {step + 1} / {STEPS.length}
          </span>
          {step < STEPS.length - 1 ? (
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

function Row({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-white/50">{label}</dt>
      <dd className="flex-1 text-white/90">{value || '—'}</dd>
    </div>
  )
}
