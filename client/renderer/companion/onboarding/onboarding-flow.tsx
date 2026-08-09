import { useStore } from '@nanostores/react'
import * as React from 'react'
import { useEffect, useRef, useState } from 'react'

import { pickAvatarImage, type PickedImage } from '@/companion/avatar-image'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { INPUT_CLASS } from '@/companion/input-class'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import {
  APPEARANCE_PRESETS,
  CHARACTER_GENDER_PRESETS,
  PERSONALITY_PRESETS,
  ROLE_PRESETS,
  SPEAKING_STYLE_PRESETS,
  SPECIES_PRESETS,
  USER_AGE_BUCKET_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} from '@/companion/persona-presets'
import { TWO_STEP_INTRO_HINT } from '@/companion/portrait-flow-copy'
import { $activeAvatarId, $regenFeedback, applyPortrait, setRegenFeedback } from '@/companion/portrait-store'
import { useRegeneratePortrait } from '@/companion/use-regenerate-portrait'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { sleep } from '@/shared/lib/utils'
import { $gatewayState } from '@/shared/store/gateway'

import {
  assembleCharacterPersona,
  assemblePersona,
  MAX_APPEARANCE,
  MAX_USER_TEXT,
  type OnboardingAnswers
} from '../persona'
import { setCompanionVoiceId } from '../prefs'
import { speak, stopSpeaking } from '../tts'
import { fetchVoiceCatalog, matchVoicePreference, nextVoice, sampleLine, type VoiceOption } from '../voice'
import { $voicePreparing } from '../voice-state'

import { playOnboardingAudio } from './onboarding-audio'
import { Chip, PortraitPanel } from './onboarding-components'

type Phase =
  | 'q-character'
  | 'hatching'
  | 'portrait-avatar'
  | 'portrait-fullbody'
  | 'q-user'
  | 'voice'
  | 'finishing'
  | 'greeting'

type VoiceStage = 'describe' | 'catalog'
type VoiceLanguageFilter = '' | 'zh' | 'en'

const VOICE_LANGUAGE_TABS: { id: VoiceLanguageFilter; label: string }[] = [
  { id: '', label: '全部' },
  { id: 'zh', label: '中文' },
  { id: 'en', label: 'English' }
]

type QKey = keyof OnboardingAnswers

// A chip that picks *what kind* of answer the user is about to give instead of
// being the answer itself — see CALL_NAME_KINDS.
interface AnswerKind {
  chip: string
  label: string
  placeholder: string
  values?: readonly string[]
}

interface Question {
  key: QKey
  text: string
  placeholder: string
  required: boolean
  multiline: boolean
  presets?: readonly string[]
  max?: number
  // Lets the user hand over a reference image alongside the text answer.
  allowImage?: boolean
  // Mutually exclusive with `presets`: two-level entry instead of chip-fills-input.
  kinds?: readonly AnswerKind[]
}

// "名字 / 昵称" are categories of appellation, not appellations — filling the
// input with the literal chip text would store "昵称" as the way to address the
// user. Picking a chip re-labels the input and asks for the concrete value;
// 称号 additionally offers ready-made values because those *are* answers.
const CALL_NAME_KINDS: readonly AnswerKind[] = [
  { chip: '名字', label: '那，您的名字是？', placeholder: '比如：张三' },
  { chip: '昵称', label: '那，您的昵称是？', placeholder: '比如：小明、阿棠' },
  {
    chip: '称号',
    label: '想让我用哪个称号？',
    placeholder: '或者自己写一个…',
    values: ['老板', '主人', '老师', '大人']
  },
  { chip: '自填', label: '那，想让我怎么叫您？', placeholder: '随便写，我记住就是了…' }
]

const QUESTIONS: readonly Question[] = [
  {
    key: 'name',
    text: '您好…我还不认识自己。您愿意给我一个名字吗？',
    placeholder: '给我起个名字吧',
    required: true,
    multiline: false
  },
  {
    key: 'species',
    text: '那我是哪种生灵呢？',
    placeholder: '如：精灵、人类、龙…（可直接输入或选择标签）',
    required: true,
    multiline: false,
    presets: SPECIES_PRESETS
  },
  {
    key: 'character_gender',
    text: '嗯…那我是男性、女性、还是…',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    presets: CHARACTER_GENDER_PRESETS
  },
  {
    key: 'appearance',
    text: '那您希望我长什么样？说说头发、眼睛、穿着、气质…',
    placeholder: '比如：金发绿眼、黑色礼帽…',
    required: false,
    multiline: true,
    max: MAX_APPEARANCE,
    presets: APPEARANCE_PRESETS,
    allowImage: true
  },
  {
    key: 'role',
    text: '好的，那您希望我是什么样的身份？',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    presets: ROLE_PRESETS
  },
  {
    key: 'personality',
    text: '您希望我是什么性格？',
    placeholder: '自由描述…',
    required: false,
    multiline: false,
    presets: PERSONALITY_PRESETS
  },
  {
    key: 'user_call_name',
    text: '我该怎么称呼您？',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    max: MAX_USER_TEXT,
    kinds: CALL_NAME_KINDS
  },
  {
    key: 'user_gender',
    text: '您方便告诉我您的性别吗？',
    placeholder: '或自由描述…',
    required: false,
    multiline: false,
    max: MAX_USER_TEXT,
    presets: USER_GENDER_PRESETS
  },
  {
    key: 'user_age_bucket',
    text: '您属于哪个年龄段？',
    placeholder: '或自由描述…',
    required: false,
    multiline: false,
    max: MAX_USER_TEXT,
    presets: USER_AGE_BUCKET_PRESETS
  },
  {
    key: 'user_hobbies',
    text: '您平时喜欢什么？',
    placeholder: '可以多写几个…',
    required: false,
    multiline: true,
    max: MAX_USER_TEXT
  },
  // speaking_style is required by the backend schema — the dedicated
  // question makes the user's choice the direct source of truth.
  {
    key: 'speaking_style',
    text: '您希望我说话的风格是什么样的？',
    placeholder: '比如：简短、爱用比喻、俏皮一点…',
    required: false,
    multiline: true,
    max: 500,
    presets: SPEAKING_STYLE_PRESETS
  },
  {
    key: 'user_freeform',
    text: '还有什么想告诉我、或者想叮嘱我的吗？',
    placeholder: '可跳过…',
    required: false,
    multiline: true,
    max: MAX_USER_TEXT
  },
  {
    key: 'voice',
    text: '您希望我听起来是什么样的？比如温柔的少女音、沉稳的男声、活泼的正太…',
    placeholder: '描述你想要的声音…',
    required: false,
    multiline: false,
    presets: VOICE_PRESETS
  }
]

// Slice boundaries mirror backend ONBOARDING_FIELDS order — required for resume routing.
const CHARACTER_QUESTIONS: readonly Question[] = QUESTIONS.slice(0, 6) // name, species, character_gender, appearance, role, personality
const USER_QUESTIONS: readonly Question[] = QUESTIONS.slice(6, 12) // user_call_name, user_gender, user_age_bucket, user_hobbies, speaking_style, user_freeform
const VOICE_QUESTIONS: readonly Question[] = QUESTIONS.slice(12, 13) // voice

const PHASE_QUESTIONS: Record<Phase, readonly Question[]> = {
  'q-character': CHARACTER_QUESTIONS,
  'q-user': USER_QUESTIONS,
  voice: VOICE_QUESTIONS,
  hatching: [],
  'portrait-avatar': [],
  'portrait-fullbody': [],
  finishing: [],
  greeting: []
}

// Routes resume's next_field to q-user vs voice. Mirrors backend.services.companion._POST_CHARACTER_FIELDS.
const POST_CHARACTER_FIELDS: ReadonlySet<string> = new Set([
  'user_call_name',
  'user_gender',
  'user_age_bucket',
  'user_hobbies',
  'user_freeform',
  'speaking_style'
])

// Hoisted: useInteractiveRegion's effect otherwise re-registers every render.
const interactiveRegionRect = (el: HTMLElement): DOMRect | null => {
  const rect = el.getBoundingClientRect()

  return rect.width === 0 || rect.height === 0 ? null : rect
}

// Throws from `fn` propagate so callers can rethrow 4xx and short-circuit retries.
const retryTransient = async <T,>(
  fn: () => Promise<T | null | undefined>,
  delayMs: number,
  maxAttempts = 3
): Promise<T | null> => {
  for (let i = 0; i < maxAttempts; i++) {
    const result = await fn()

    if (result) {
      return result
    }

    if (i < maxAttempts - 1) {
      await sleep(delayMs)
    }
  }

  return null
}

const DRAG_THRESHOLD = 6

// Desktop answer keys → Backend ONBOARDING_FIELDS. species / character_gender
// become biological_type / gender via assemblePersona; user_* travel in the
// same PUT and the backend routes them to Memory.
const BACKEND_FIELD: Record<QKey, string> = {
  name: 'name',
  species: 'species',
  character_gender: 'character_gender',
  appearance: 'appearance',
  role: 'role',
  personality: 'personality',
  speaking_style: 'speaking_style',
  user_call_name: 'user_call_name',
  user_gender: 'user_gender',
  user_age_bucket: 'user_age_bucket',
  user_hobbies: 'user_hobbies',
  user_freeform: 'user_freeform',
  voice: 'voice'
}

// Step-1: avatar only. Returns the raw backend response (id is captured for
// step 2's fullbody call). applyPortrait owns the resolve step.
async function generatePortrait(
  reference: PickedImage | null
): Promise<{ asset_url?: string; seed_url?: string | null; id?: number } | null> {
  try {
    const res = await window.deskagent.api<{ asset_url?: string; seed_url?: string | null; id?: number }>({
      path: reference ? '/api/companion/avatar/from-image' : '/api/companion/avatar',
      method: 'POST',
      body: reference ? { content_type: reference.contentType, image: reference.base64 } : {}
    })

    return res
  } catch (error) {
    // Rethrow deterministic failures so retryTransient doesn't burn the 120s avatar budget.
    if (isClientErrorIpc(error)) {
      throw error
    }

    return null
  }
}

// Step-2: full-body seed on top of the just-confirmed avatar row.
async function generateFullbody(avatarId: number): Promise<{ id?: number; seed_url?: string } | null> {
  try {
    const res = await window.deskagent.api<{ id?: number; seed_url?: string }>({
      path: `/api/companion/avatar/${avatarId}/fullbody`,
      method: 'POST'
    })

    return res
  } catch (error) {
    if (isClientErrorIpc(error)) {
      throw error
    }

    return null
  }
}

async function savePersona(payload: ReturnType<typeof assemblePersona>): Promise<boolean> {
  try {
    await window.deskagent.api({
      path: '/api/companion/persona',
      method: 'PUT',
      body: { definition_json: JSON.stringify(payload) }
    })

    return true
  } catch (error) {
    // Rethrow 4xx so retryTransient doesn't burn retries on a deterministic failure.
    if (isClientErrorIpc(error)) {
      throw error
    }

    return false
  }
}

interface OnboardingFlowProps {
  onCompleted: () => void
}

// Lives outside OnboardingFlow: its $regenFeedback subscription would otherwise re-render the whole dialog on every keystroke.
function RegenFeedbackInput({ disabled }: { disabled: boolean }): React.JSX.Element {
  const value = useStore($regenFeedback)

  return (
    <textarea
      className={`${INPUT_CLASS} text-xs`}
      disabled={disabled}
      maxLength={MAX_APPEARANCE}
      onChange={e => setRegenFeedback(e.target.value)}
      placeholder="哪里不满意？比如：头发再短一点、眼睛再大一点、表情更温和…（可留空直接重新生成）"
      rows={2}
      value={value}
    />
  )
}

export function OnboardingFlow({ onCompleted }: OnboardingFlowProps): React.JSX.Element | null {
  const gatewayState = useStore($gatewayState)
  const voicePreparing = useStore($voicePreparing)
  const { requestGateway } = useGatewayRequest()
  const [phase, setPhase] = useState<Phase>('q-character')
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [seedUrl, setSeedUrl] = useState<string | null>(null)
  // Active avatar row id is published to the global $activeAvatarId atom by
  // applyPortrait — subscribe to it so any step-1 regen propagates without
  // us wiring setState through every call site.
  const activeAvatarId = useStore($activeAvatarId)
  // voice phase runs the Q13 description input first, then the catalogue picker.
  const [voiceStage, setVoiceStage] = useState<VoiceStage>('describe')

  // Failure keeps the current portrait: it already holds resolved bytes.
  // The shared `applyPortrait` writes the global $portraitUrl + $activeAvatarId
  // atoms; we mirror to local seedUrl state since the seed pane isn't a
  // global concern.
  const applyLocalPortrait = async (
    response: { asset_url?: string | null; seed_url?: string | null; id?: number } | null | undefined
  ): Promise<{ avatar: string | null; id: number | null }> => {
    const { avatar, seed } = await applyPortrait({
      id: response?.id,
      assetUrl: response?.asset_url,
      seedUrl: response?.seed_url
    })

    if (avatar) {
      setPortraitUrl(avatar)
    }

    setSeedUrl(seed)

    return { avatar, id: response?.id ?? null }
  }

  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  const [voiceLangFilter, setVoiceLangFilter] = useState<VoiceLanguageFilter>('zh')
  // Failure hints live on the portrait panel — the form area is hidden behind it.
  const [portraitPanelHint, setPortraitPanelHint] = useState<string | null>(null)
  // Step-2 transition has no avatar regen hook attached (the user already
  // confirmed the face), so track its loading here for button-disabled state.
  const [fullbodyLoading, setFullbodyLoading] = useState(false)

  // Resume path skips enterHatching, so the first-time-only portraitUrl state never gets set; re-pull here so voice-catalog PortraitPanel isn't empty. 404 (no portrait yet) is a no-op.
  const hydrateLocalPortrait = async () => {
    try {
      const res = await window.deskagent.api<{ asset_url?: string; seed_url?: string }>({
        path: '/api/companion/avatar'
      })

      if (!res?.asset_url && !res?.seed_url) {
        return
      }

      const applied = await applyPortrait({ assetUrl: res.asset_url, seedUrl: res.seed_url })

      if (applied.avatar) {
        setPortraitUrl(applied.avatar)
      }

      setSeedUrl(applied.seed)
    } catch (error) {
      if (!isClientErrorIpc(error)) {
        console.warn('[onboarding] portrait hydrate failed', error)
      }
    }
  }

  // Reference image handed over at the 形象描述 question. Session-scoped on
  // purpose — `onboarding.submit` persists text answers only, so a resumed
  // draft asks for the image again rather than silently generating without it.
  const [refImage, setRefImage] = useState<PickedImage | null>(null)
  const [answerKind, setAnswerKind] = useState<AnswerKind | null>(null)

  const [hint, setHint] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const resumedRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const dragRef = useRef<{
    startX: number
    startY: number
    originX: number
    originY: number
    moved: boolean
    pointerId: number
  } | null>(null)

  // Centered initial position; the user can drag from there.
  const [dialogPos, setDialogPos] = useState<{ x: number; y: number }>(() => {
    const width = 448
    const height = 600

    if (typeof window === 'undefined') {
      return { x: 0, y: 0 }
    }

    return {
      x: Math.max(0, Math.round((window.innerWidth - width) / 2)),
      y: Math.max(0, Math.round((window.innerHeight - height) / 2))
    }
  })

  // Onboarding dialog is fully interactive — register its actual visible rect
  // with the global interactive-regions registry so SpriteStage's hit-test
  // captures only while the cursor is over the dialog form card.
  // SpriteStage restores click-through on unmount.
  useInteractiveRegion('onboarding', containerRef, interactiveRegionRect)

  useEffect(() => {
    return () => {
      stopSpeaking()
    }
  }, [])

  // Drag uses document-level listeners (not React onPointerMove on the
  // container) so the drag survives the cursor leaving the dialog rect and
  // still updates while the cursor is over an unrelated region. setPointerCapture
  // would interfere with click events fired on the form's buttons/inputs.
  const onDialogPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    const target = e.target as HTMLElement

    if (target.closest('button, input, textarea, [contenteditable="true"]')) {
      return
    }

    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originX: dialogPos.x,
      originY: dialogPos.y,
      moved: false,
      pointerId: e.pointerId
    }
  }

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current

      if (!drag || drag.pointerId !== e.pointerId) {
        return
      }

      const dx = e.clientX - drag.startX
      const dy = e.clientY - drag.startY

      if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) {
        return
      }

      drag.moved = true
      setDialogPos({ x: drag.originX + dx, y: drag.originY + dy })
    }

    const onUp = (e: PointerEvent) => {
      const drag = dragRef.current

      if (!drag || drag.pointerId !== e.pointerId) {
        return
      }

      dragRef.current = null
    }

    const onLeave = (e: PointerEvent) => {
      // Pointer leaving the window mid-drag clears the drag state so subsequent
      // moves don't translate with stale origin coordinates.
      const drag = dragRef.current

      if (drag && drag.pointerId === e.pointerId) {
        dragRef.current = null
      }
    }

    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('pointercancel', onUp)
    document.addEventListener('pointerleave', onLeave)

    return () => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('pointercancel', onUp)
      document.removeEventListener('pointerleave', onLeave)
    }
  }, [])

  // Breakpoint recovery (plan §3 / design §7.5): once the gateway is open,
  // pull any half-answered draft so a crash/exit mid-onboarding resumes from
  // the next unanswered question. One-shot — never re-resumes.
  useEffect(() => {
    if (resumedRef.current || gatewayState !== 'open') {
      return
    }

    resumedRef.current = true

    void (async () => {
      try {
        const state = await requestGateway<{
          answers?: Record<string, string>
          next_field?: string | null
          complete?: boolean
        }>('onboarding.get_state', {})

        if (state?.complete) {
          onCompleted()

          return
        }

        if (state?.answers) {
          // Merge server draft with answers typed in the current session;
          // local non-empty edits win so recent user intent survives.
          const a = state.answers
          setAnswers(prev => {
            const next: OnboardingAnswers = { ...prev }

            for (const k of Object.keys(a) as (keyof OnboardingAnswers)[]) {
              if (next[k] == null || next[k] === '') {
                next[k] = a[k] as never
              }
            }

            return next
          })

          const nextField = state.next_field

          if (nextField === 'voice') {
            // Skip Q13 description — already answered.
            setPhase('voice')
            setVoiceStage('catalog')
            setQIndex(0)
            void hydrateLocalPortrait()
          } else if (nextField && POST_CHARACTER_FIELDS.has(nextField)) {
            const idx = USER_QUESTIONS.findIndex(q => BACKEND_FIELD[q.key] === nextField)
            setPhase('q-user')
            setQIndex(Math.max(0, idx))
          } else if (nextField) {
            const idx = CHARACTER_QUESTIONS.findIndex(q => BACKEND_FIELD[q.key] === nextField)
            setPhase('q-character')
            setQIndex(Math.max(0, idx))
          }
        }
      } catch {
        /* no draft yet — start fresh */
      }

      setVoiceCatalog((await fetchVoiceCatalog(requestGateway)).voices)
    })()
  }, [gatewayState, requestGateway, onCompleted])

  const currentList = PHASE_QUESTIONS[phase]

  const question = currentList[qIndex]
  // Latest-answers ref so the speak/focus effects only re-run on phase/qIndex,
  // not on every keystroke (the rule's exhaustive-deps lint can't see the
  // intent).
  const answersRef = useLatestRef(answers)

  // Question text rendered under the input.
  const spokenText = question?.text ?? ''

  // Speak each question as it appears (default neutral voice; plan §3.2).
  useEffect(() => {
    if (phase !== 'q-character' && phase !== 'q-user' && phase !== 'voice') {
      return
    }

    const q = currentList[qIndex]

    if (!q) {
      return
    }

    const current = answersRef.current
    const initialVal = (current[q.key] as string) ?? ''
    setInput(initialVal)
    setAnswerKind(null)
    setHint(null)

    // Audio manifest tags align with the original QUESTIONS positions, not the slice-local qIndex.
    const globalQIndex =
      phase === 'q-character'
        ? qIndex
        : phase === 'q-user'
          ? qIndex + CHARACTER_QUESTIONS.length
          : qIndex + CHARACTER_QUESTIONS.length + USER_QUESTIONS.length

    void playOnboardingAudio(`onboarding.q${globalQIndex}`)

    return () => stopSpeaking()
  }, [phase, qIndex, currentList, answersRef])

  useEffect(() => {
    const isQuestionPhase = phase === 'q-character' || phase === 'q-user' || phase === 'voice'

    if (isQuestionPhase && currentList[qIndex]) {
      ;(currentList[qIndex].multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, qIndex, currentList])

  const commit = (value: string | undefined) => {
    const q = currentList[qIndex]

    if (!q) {
      return
    }

    const trimmed = value && value.trim() ? value.trim() : undefined
    const cleaned = trimmed && q.max ? trimmed.slice(0, q.max) : trimmed
    setAnswers((prev: OnboardingAnswers) => ({ ...prev, [q.key]: cleaned }))

    // Per-field incremental persistence (design §7.5); fire-and-forget — never
    // block the UI on a draft save. No-op until the gateway is open.
    if (gatewayState === 'open') {
      void requestGateway('onboarding.submit', { field: BACKEND_FIELD[q.key], value: cleaned ?? null }).catch(() => {})
    }
  }

  const advance = () => {
    // Voice describe has a single question; advancing flips to catalog which the useEffect below loads.
    if (phase === 'voice' && voiceStage === 'describe') {
      setVoiceStage('catalog')

      return
    }

    if (qIndex < currentList.length - 1) {
      setQIndex(qIndex + 1)

      return
    }

    if (phase === 'q-character') {
      void enterHatching()
    } else if (phase === 'q-user') {
      setPhase('voice')
      setQIndex(0)
      setVoiceStage('describe')
    }
  }

  // Loads catalogue + preview TTS on describe→catalog transition (and on resume into catalog).
  useEffect(() => {
    if (phase !== 'voice' || voiceStage !== 'catalog') {
      return
    }
    void (async () => {
      stopSpeaking()
      const matched = await matchVoicePreference(requestGateway, answers.voice ?? '')
      setVoice(matched.voice)
      setCompanionVoiceId(matched.voice.id)
      setVoiceLangFilter('zh')
      const catalog = await fetchVoiceCatalog(requestGateway, 'zh')
      setVoiceCatalog([
        matched.voice,
        ...matched.alternatives,
        ...catalog.voices.filter(v => v.id !== matched.voice.id)
      ])
      void speak(sampleLine(answers.name || ''), matched.voice.id || undefined, 'onboarding.voice.preview')
    })()
  }, [phase, voiceStage, requestGateway, answers.voice, answers.name])

  const onSend = () => {
    const q = currentList[qIndex]

    if (q?.required && !input.trim()) {
      setHint('名字是必填的哦～')

      return
    }

    commit(input)
    advance()
  }

  const onSkip = () => {
    if (question?.required) {
      return
    }

    commit(undefined)
    advance()
  }

  const onBack = () => {
    // Defense-in-depth: JSX already disables at qIndex === 0; guard keeps the invariant if state diverges.
    if (qIndex === 0) {
      return
    }

    setQIndex(qIndex - 1)
  }

  const enterHatching = async () => {
    setPhase('hatching')
    setHint(null)
    void playOnboardingAudio('onboarding.hatching')

    // Finalize persona before portrait — avatar gen needs is_complete=true; user_* are routed to Memory later via submit_onboarding_field.
    // savePersona re-throws 4xx; roll back to the form so the user can fix the field.
    let personaOk = false

    try {
      personaOk = (await retryTransient(() => savePersona(assembleCharacterPersona(answers)), 700)) === true
    } catch (err) {
      setPhase('q-character')
      setHint(err instanceof Error ? `记忆存不上：${err.message}` : '记忆存不上，请重试 onboarding')
      void playOnboardingAudio('onboarding.hatching.retry')

      return
    }

    let url: string | null = null

    if (personaOk) {
      try {
        const applied = await applyLocalPortrait(await retryTransient(() => generatePortrait(refImage), 1500, 2))
        url = applied.avatar
      } catch {
        // A deterministic 4xx (unusable reference image, incomplete persona)
        // must not strand the flow on 'hatching' — fall through to the portrait
        // phase, where regenerate with optional feedback is still available.
        url = null
      }

      if (!url) {
        // The portrait panel is what renders next; `hint` is only visible in the form.
        setPortraitPanelHint(refImage ? '这张参考图我没能用上…待会儿再换一张吧' : '我还没想好…')
      }
    } else {
      setHint('记忆还没存好，稍后再试试形象吧…')
    }

    // Step 1: avatar only. The fullbody step is triggered explicitly via
    // 「下一步」 so the user reviews the face before locking in the body.
    setPhase('portrait-avatar')
    void playOnboardingAudio(url ? 'onboarding.portrait.ok' : 'onboarding.portrait.failed')
  }

  // Step 1 — avatar regen: creates a new avatar row, the new id publishes to
  // ``$activeAvatarId`` automatically (via applyPortrait inside the hook).
  const { regenerate: regenerateAvatarPortrait, busy: avatarBusy } = useRegeneratePortrait({
    refImage,
    step: 'avatar',
    playAudioOnSuccess: true,
    onRegenerated: ({ avatar, seed }) => {
      if (avatar) {
        setPortraitUrl(avatar)
      }

      setSeedUrl(seed)
    }
  })

  // Step 2 — fullbody regen: re-runs the seed step on top of the latest
  // confirmed avatar row. ``avatarId`` flows from the atom, not local state.
  const { regenerate: regenerateFullbodyPortrait, busy: fullbodyBusy } = useRegeneratePortrait({
    step: 'fullbody',
    avatarId: activeAvatarId,
    playAudioOnSuccess: true,
    onRegenerated: ({ avatar, seed }) => {
      if (avatar) {
        setPortraitUrl(avatar)
      }

      setSeedUrl(seed)
    }
  })

  const advanceToFullbody = async () => {
    if (activeAvatarId == null) {
      setPortraitPanelHint('找不到对应的形象，请稍后重试')

      return
    }

    setPortraitPanelHint(null)
    setFullbodyLoading(true)
    const idAtCall = activeAvatarId
    let res: { id?: number; seed_url?: string } | null = null

    try {
      res = await retryTransient(() => generateFullbody(idAtCall), 1500, 2)
    } catch {
      res = null
    } finally {
      setFullbodyLoading(false)
    }

    if (!res?.seed_url) {
      setPortraitPanelHint('全身图暂时没生成出来，可以再点一次「下一步」试试')

      return
    }

    // Only the seed URL is new — the avatar hasn't moved. Pass asset_url: null
    // so applyPortrait keeps the existing $portraitUrl without re-resolving
    // the data URL through the IPC layer.
    await applyLocalPortrait({ asset_url: null, seed_url: res.seed_url, id: idAtCall })
    setPhase('portrait-fullbody')
  }

  const backToAvatar = () => {
    setPhase('portrait-avatar')
  }

  const pickReferenceImage = async () => {
    const picked = await pickAvatarImage('选择一张参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    setRefImage(picked.image)
    setHint(null)
  }

  const confirmPortrait = async () => {
    setRefImage(null)
    // Portrait confirmed → user info (Q7-Q12) before voice, so any portrait regen overlaps with q-user typing.
    setPhase('q-user')
    setQIndex(0)
    setInput('')
    setAnswerKind(null)
    setHint(null)
  }

  const onVoiceLangTabClick = async (lang: VoiceLanguageFilter) => {
    setVoiceLangFilter(lang)
    const catalog = await fetchVoiceCatalog(requestGateway, lang)
    setVoiceCatalog(catalog.voices)
    // Reset the current voice to the first of the filtered list so the
    // Try/Next cycle starts from a language-appropriate default. The
    // persisted voice id follows the displayed voice so a later
    // confirmVoice picks the filtered-list voice, not the previous tab's.
    const next = catalog.voices[0] ?? voice
    setVoice(next)

    if (next) {
      setCompanionVoiceId(next.id)
    }
  }

  const confirmVoice = () => {
    setPhase('finishing')
    void finish()
  }

  const finish = async () => {
    // Safety-net retry; roll back to 'voice' on failure so phase isn't stuck on 'finishing'.
    try {
      await savePersona(assemblePersona(answers))
    } catch (err) {
      setPhase('voice')
      setHint(err instanceof Error ? `同步失败：${err.message}` : '同步失败，请稍后再试')
      void playOnboardingAudio('onboarding.finishing.retry')

      return
    }

    setPhase('greeting')

    const ok = await playOnboardingAudio('onboarding.greeting')

    if (!ok) {
      setHint('（声音暂时不可用）')
    }

    await sleep(ok ? 600 : 1800)
    onCompleted()
  }

  const presetValues = question?.presets ?? []

  return (
    <div className="fixed inset-0 z-50 pointer-events-none" style={{ pointerEvents: 'none' }}>
      <div
        className="absolute flex max-h-[90vh] w-full max-w-md flex-col items-center gap-4"
        onPointerDown={onDialogPointerDown}
        ref={containerRef}
        style={{
          left: dialogPos.x,
          padding: '0 1.5rem',
          pointerEvents: 'auto',
          position: 'absolute',
          top: dialogPos.y,
          touchAction: 'none'
        }}
      >
        <div
          className="w-full rounded-2xl border border-white/10 bg-black/45 p-5 text-white shadow-2xl backdrop-blur-md"
          style={{ pointerEvents: 'auto' }}
        >
          {voicePreparing && <p className="mb-2 text-center text-[10px] text-white/40">🔊 正在准备声音…</p>}
          {(phase === 'q-character' || phase === 'q-user' || (phase === 'voice' && voiceStage === 'describe')) &&
            question && (
              <>
                <p className="min-h-[3.5rem] text-[15px] leading-relaxed">{spokenText}</p>
                {presetValues.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {presetValues.map(p => (
                      <Chip active={input === p} key={p} label={p} onClick={() => setInput(p)} />
                    ))}
                  </div>
                )}
                {question.kinds && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {question.kinds.map(k => (
                      <Chip
                        active={answerKind?.chip === k.chip}
                        key={k.chip}
                        label={k.chip}
                        onClick={() => {
                          setAnswerKind(k)
                          setInput('')
                          inputRef.current?.focus()
                        }}
                      />
                    ))}
                  </div>
                )}
                {answerKind && (
                  <>
                    <p className="mt-3 text-xs text-white/55">{answerKind.label}</p>
                    {answerKind.values && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {answerKind.values.map(v => (
                          <Chip active={input === v} key={v} label={v} onClick={() => setInput(v)} />
                        ))}
                      </div>
                    )}
                  </>
                )}
                {question.multiline ? (
                  <textarea
                    className={`mt-3 ${INPUT_CLASS} text-sm`}
                    onChange={e => setInput(e.target.value)}
                    placeholder={question.placeholder}
                    ref={textareaRef}
                    rows={3}
                    value={input}
                  />
                ) : (
                  <input
                    className="mt-3 w-full rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm outline-none placeholder:text-white/40 focus:border-white/40"
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && !question.multiline) {
                        onSend()
                      }
                    }}
                    placeholder={answerKind?.placeholder ?? question.placeholder}
                    ref={inputRef}
                    value={input}
                  />
                )}
                {question.allowImage && (
                  <div className="mt-3 flex items-center gap-2 text-xs">
                    <button
                      className="rounded-full border border-dashed border-white/25 px-3 py-1 text-white/70 transition hover:bg-white/10"
                      onClick={() => void pickReferenceImage()}
                      type="button"
                    >
                      {refImage ? '换一张参考图' : '＋ 上传参考图'}
                    </button>
                    {refImage && (
                      <>
                        <img alt="参考图" className="h-9 w-9 rounded-md object-cover" src={refImage.previewUrl} />
                        <span className="text-[10px] text-white/35">我会照着它画自己</span>
                        <button
                          className="ml-auto text-white/40 transition hover:text-white"
                          onClick={() => setRefImage(null)}
                          type="button"
                        >
                          移除
                        </button>
                      </>
                    )}
                  </div>
                )}
                <div className="mt-4 flex items-center justify-between text-xs">
                  <button
                    className="text-white/60 transition hover:text-white disabled:opacity-30"
                    disabled={qIndex === 0}
                    onClick={onBack}
                    type="button"
                  >
                    上一题
                  </button>
                  <div className="flex gap-3">
                    {!question.required && (
                      <button className="text-white/60 transition hover:text-white" onClick={onSkip} type="button">
                        跳过
                      </button>
                    )}
                    <button
                      className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white"
                      onClick={onSend}
                      type="button"
                    >
                      {qIndex === currentList.length - 1 ? '完成' : '发送'}
                    </button>
                  </div>
                </div>
                {hint && <p className="mt-2 text-xs text-amber-300/80">{hint}</p>}
                <p className="mt-2 text-right text-[10px] text-white/30">
                  {qIndex + 1} / {currentList.length}
                </p>
              </>
            )}

          {phase === 'hatching' && (
            <p className="py-6 text-center text-sm text-white/80">{hint || '让我想想我该是什么样子…'}</p>
          )}

          {(phase === 'portrait-avatar' ||
            phase === 'portrait-fullbody' ||
            phase === 'greeting' ||
            (phase === 'voice' && voiceStage === 'catalog')) && (
            <PortraitPanel
              avatarUrl={portraitUrl}
              hint={portraitPanelHint}
              introHint={phase === 'portrait-avatar' ? TWO_STEP_INTRO_HINT : null}
              name={answers.name?.trim() || '伙伴'}
              seedUrl={seedUrl}
              step={phase === 'portrait-avatar' ? 'avatar' : 'fullbody'}
            />
          )}

          {phase === 'portrait-avatar' && (
            <div className="mt-4">
              <RegenFeedbackInput disabled={avatarBusy} />
              <div className="mt-3 flex items-center justify-between text-xs">
                <button
                  className="text-white/70 transition hover:text-white disabled:opacity-40"
                  disabled={avatarBusy}
                  onClick={() => void regenerateAvatarPortrait()}
                  type="button"
                >
                  {avatarBusy ? '生成中…' : '重新生成'}
                </button>
                <button
                  className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white"
                  disabled={avatarBusy || fullbodyLoading || activeAvatarId == null}
                  onClick={() => void advanceToFullbody()}
                  type="button"
                >
                  {fullbodyLoading ? '生成中…' : '下一步'}
                </button>
              </div>
              {portraitPanelHint && <p className="mt-2 text-xs text-rose-300/90">{portraitPanelHint}</p>}
            </div>
          )}

          {phase === 'portrait-fullbody' && (
            <div className="mt-4">
              <RegenFeedbackInput disabled={fullbodyBusy} />
              <div className="mt-3 flex items-center justify-between text-xs">
                <div className="flex gap-3">
                  <button
                    className="text-white/60 transition hover:text-white disabled:opacity-40"
                    disabled={fullbodyBusy}
                    onClick={backToAvatar}
                    type="button"
                  >
                    上一步
                  </button>
                  <button
                    className="text-white/70 transition hover:text-white disabled:opacity-40"
                    disabled={fullbodyBusy}
                    onClick={() => void regenerateFullbodyPortrait()}
                    type="button"
                  >
                    {fullbodyBusy ? '生成中…' : '重新生成'}
                  </button>
                </div>
                <button
                  className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white"
                  disabled={fullbodyBusy}
                  onClick={confirmPortrait}
                  type="button"
                >
                  就这样吧
                </button>
              </div>
              {portraitPanelHint && <p className="mt-2 text-xs text-rose-300/90">{portraitPanelHint}</p>}
            </div>
          )}

          {phase === 'voice' && voiceStage === 'catalog' && voice && (
            <div className="mt-4">
              <div className="mb-3 flex gap-1 rounded-full border border-white/10 bg-white/5 p-1 text-[10px]">
                {VOICE_LANGUAGE_TABS.map(tab => (
                  <button
                    className={`flex-1 rounded-full px-2 py-1 transition ${voiceLangFilter === tab.id ? 'bg-white/90 text-black' : 'text-white/60 hover:text-white'}`}
                    key={tab.id || 'all'}
                    onClick={() => void onVoiceLangTabClick(tab.id)}
                    type="button"
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <div className="flex items-center justify-between text-xs text-white/70">
                <span>{voice.label}</span>
                <div className="flex gap-3">
                  <button
                    className="transition hover:text-white disabled:opacity-40"
                    disabled={voicePreparing}
                    onClick={() =>
                      void speak(sampleLine(answers.name || ''), voice?.id || undefined, 'onboarding.voice.preview.try')
                    }
                    type="button"
                  >
                    试听
                  </button>
                  <button
                    className="transition hover:text-white disabled:opacity-40"
                    disabled={voicePreparing}
                    onClick={() => {
                      const n = nextVoice(voice.id, voiceCatalog.length ? voiceCatalog : [voice])
                      setVoice(n)
                      setCompanionVoiceId(n.id)
                      void speak(sampleLine(answers.name || ''), n.id || undefined, 'onboarding.voice.preview.next')
                    }}
                    type="button"
                  >
                    换一个
                  </button>
                </div>
              </div>
              <p className="mt-1 text-[10px] text-white/40">
                {voiceCatalog.length} 个音色 · 先挑个差不多的就行，以后随时能在设置里调。
              </p>
              <button
                className="mt-3 w-full rounded-full bg-white/90 py-1.5 text-sm font-medium text-black transition hover:bg-white"
                onClick={confirmVoice}
                type="button"
              >
                使用这个
              </button>
            </div>
          )}

          {phase === 'finishing' && <p className="py-6 text-center text-sm text-white/80">正在记住您…</p>}

          {phase === 'greeting' && (
            <div className="mt-4">
              <p className="text-center text-sm text-white/90">
                您好，我是{answers.name?.trim() || '您的伙伴'}。很高兴见到您！
              </p>
              {hint && <p className="mt-1 text-center text-[10px] text-white/40">{hint}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
