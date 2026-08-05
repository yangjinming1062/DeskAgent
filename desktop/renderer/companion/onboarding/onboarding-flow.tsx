import { useStore } from '@nanostores/react'
import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { awaitAvatarRegeneration } from '@/companion/avatar-regen-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
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
import { $gatewayState } from '@/shared/store/gateway'

import { clearClipCatalog, playTransitionClip } from '../clip-store'
import { assemblePersona, MAX_APPEARANCE, MAX_USER_TEXT, type OnboardingAnswers } from '../persona'
import { setCompanionVoiceId } from '../prefs'
import { Silhouette } from '../sprite/silhouette'
import { speak, stopSpeaking } from '../tts'
import { fetchVoiceCatalog, matchVoicePreference, nextVoice, sampleLine, type VoiceOption } from '../voice'
import { $voicePreparing } from '../voice-state'

type Phase = 'q' | 'hatching' | 'portrait' | 'voice' | 'finishing' | 'greeting'
type VoiceLanguageFilter = '' | 'zh' | 'en'

const VOICE_LANGUAGE_TABS: { id: VoiceLanguageFilter; label: string }[] = [
  { id: '', label: '全部' },
  { id: 'zh', label: '中文' },
  { id: 'en', label: 'English' }
]

type QKey = keyof OnboardingAnswers

interface Question {
  key: QKey
  text: string
  placeholder: string
  required: boolean
  multiline: boolean
  presets: readonly string[]
  max?: number
}

const QUESTIONS: readonly Question[] = [
  {
    key: 'name',
    text: '您好…我还不认识自己。您愿意给我一个名字吗？',
    placeholder: '给我起个名字吧',
    required: true,
    multiline: false,
    presets: []
  },
  {
    key: 'species',
    text: '那我是哪种生灵呢？',
    placeholder: '或者自由描述…',
    required: false,
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
    presets: APPEARANCE_PRESETS
  },
  {
    key: 'role',
    text: '好的，我会是 {name}。那您希望我是什么样的身份？',
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
    presets: ['名字', '昵称', '老板', '自填']
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
    max: MAX_USER_TEXT,
    presets: []
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
    max: MAX_USER_TEXT,
    presets: []
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

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

// Trailing comma disambiguates the generic from JSX in .tsx files.
const retry3 = async <T,>(fn: () => Promise<T | null | undefined>, delayMs: number): Promise<T | null> => {
  for (let i = 0; i < 3; i++) {
    const result = await fn()

    if (result) {
      return result
    }

    if (i < 2) {
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

async function generatePortrait(): Promise<string | null> {
  try {
    const res = await window.deskagent.api<{ asset_url?: string }>({
      path: '/api/companion/avatar',
      method: 'POST',
      body: { style: 'portrait' }
    })

    return res.asset_url ?? null
  } catch {
    return null
  }
}

async function savePersona(payload: ReturnType<typeof assemblePersona>): Promise<boolean> {
  try {
    await window.deskagent.api({ path: '/api/companion/persona', method: 'PUT', body: payload })

    return true
  } catch (error) {
    // Re-throw 4xx so retry3 doesn't burn 2.1s on a deterministic failure.
    // The IPC envelope ("Error invoking remote method …: Error: <body>") masks the status code — strip it first.
    const raw = error instanceof Error ? error.message : String(error)
    const unwrapped = raw.match(/Error invoking remote method '[^']+': Error: (.+)$/)?.[1] ?? raw

    if (/^4\d\d /.test(unwrapped)) {
      throw error
    }

    return false
  }
}

interface OnboardingFlowProps {
  onCompleted: () => void
}

export function OnboardingFlow({ onCompleted }: OnboardingFlowProps) {
  const gatewayState = useStore($gatewayState)
  const voicePreparing = useStore($voicePreparing)
  const { requestGateway } = useGatewayRequest()
  const [phase, setPhase] = useState<Phase>('q')
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  const [voiceLangFilter, setVoiceLangFilter] = useState<VoiceLanguageFilter>('zh')
  const [busy, setBusy] = useState(false)
  // Failure hints live on the portrait panel — the form area is hidden behind it.
  const [portraitPanelHint, setPortraitPanelHint] = useState<string | null>(null)

  // A picked base image sits in a preview state: the user can use it as-is or
  // re-render it with an optional description on top (the upload may not meet
  // the portrait seed contract, e.g. a busy background).
  const [pickedImage, setPickedImage] = useState<{ base64: string; contentType: string; previewUrl: string } | null>(
    null
  )

  const [refineDescription, setRefineDescription] = useState('')
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
  // captures only while the cursor is over the dialog silhouette + form.
  // SpriteStage restores click-through on unmount. The silhouette's CSS glow
  // (170% × 170%) overflows by ~56px on each side but stays well inside this
  // container's bounding box (silhouette is centered in the 448px row with
  // `flex items-center`), so no extra padding is needed.
  useInteractiveRegion('onboarding', containerRef, el => {
    const rect = el.getBoundingClientRect()

    return rect.width === 0 || rect.height === 0 ? null : rect
  })

  useEffect(() => {
    return () => {
      stopSpeaking()
    }
  }, [])

  // Drag uses document-level listeners (not React onPointerMove on the
  // container) so the drag survives the cursor leaving the dialog rect and
  // still updates while the cursor is over an unrelated region. setPointerCapture
  // would interfere with click events fired on the form's buttons/inputs.
  const onDialogPointerDown = (e: ReactPointerEvent) => {
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

    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('pointercancel', onUp)

    return () => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('pointercancel', onUp)
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
          // Project all 13 backend keys into OnboardingAnswers uniformly; missing
          // keys become undefined which is fine — they re-ask on resume. Legacy
          // drafts may still contain a stray ``self_intro`` key which is silently
          // ignored because it isn't an OnboardingAnswers field.
          const a = state.answers
          setAnswers({
            name: a.name,
            species: a.species,
            character_gender: a.character_gender,
            appearance: a.appearance,
            role: a.role,
            personality: a.personality,
            speaking_style: a.speaking_style,
            user_call_name: a.user_call_name,
            user_gender: a.user_gender,
            user_age_bucket: a.user_age_bucket,
            user_hobbies: a.user_hobbies,
            user_freeform: a.user_freeform,
            voice: a.voice
          })
          const idx = QUESTIONS.findIndex(q => BACKEND_FIELD[q.key] === state.next_field)

          if (idx > 0) {
            setQIndex(idx)
          }
        }
      } catch {
        /* no draft yet — start fresh */
      }

      setVoiceCatalog((await fetchVoiceCatalog(requestGateway)).voices)
    })()
  }, [gatewayState, requestGateway, onCompleted])

  const question = QUESTIONS[qIndex]
  // Latest-answers ref so the speak/focus effects only re-run on phase/qIndex,
  // not on every keystroke (the rule's exhaustive-deps lint can't see the
  // intent).
  const answersRef = useRef(answers)
  answersRef.current = answers

  // Component-scope for the render; the speak effect re-derives from answersRef so it stays out of the dep array.
  const spokenText = question?.text.replace('{name}', answers.name?.trim() || '你') ?? ''

  // Speak each question as it appears (default neutral voice; plan §3.2).
  useEffect(() => {
    if (phase !== 'q') {
      return
    }

    const q = QUESTIONS[qIndex]
    const current = answersRef.current
    setInput((current[q.key] as string) ?? '')
    setHint(null)
    const liveSpoken = q.text.replace('{name}', current.name?.trim() || '你')
    void speak(liveSpoken, undefined, `onboarding.q${qIndex}`)

    return () => stopSpeaking()
  }, [phase, qIndex])

  useEffect(() => {
    if (phase === 'q') {
      ;(QUESTIONS[qIndex].multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, qIndex])

  const commit = (value: string | undefined) => {
    const q = QUESTIONS[qIndex]
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
    if (qIndex < QUESTIONS.length - 1) {
      setQIndex(qIndex + 1)
    } else {
      void enterHatching()
    }
  }

  const onSend = () => {
    const q = QUESTIONS[qIndex]

    if (q.required && !input.trim()) {
      setHint('名字是必填的哦～')

      return
    }

    commit(input)
    advance()
  }

  const onSkip = () => {
    if (question.required) {
      return
    }

    commit(undefined)
    advance()
  }

  const onBack = () => {
    if (qIndex === 0) {
      return
    }

    setQIndex(qIndex - 1)
  }

  const enterHatching = async () => {
    setPhase('hatching')
    setHint(null)
    void speak('让我想想我该是什么样子…', undefined, 'onboarding.hatching')

    // Finalize persona before the portrait (avatar gen needs is_complete=true).
    // savePersona re-throws 4xx; roll back to the form so the user can fix the field.
    let personaOk = false

    try {
      personaOk = (await retry3(() => savePersona(assemblePersona(answers)), 700)) === true
    } catch (err) {
      setPhase('q')
      setHint(err instanceof Error ? `记忆存不上：${err.message}` : '记忆存不上，请重试 onboarding')
      void speak('刚才的回答有些问题，咱们再来一次。', undefined, 'onboarding.hatching.retry')

      return
    }

    let url: string | null = null

    if (personaOk) {
      url = await retry3(generatePortrait, 900)

      if (!url) {
        setHint('我还没想好…')
      }
    } else {
      setHint('记忆还没存好，稍后再试试形象吧…')
    }

    setPortraitUrl(url)
    setPhase('portrait')
    void speak(url ? '嗯…这就是我，您觉得怎么样？' : '我大概长这样，您觉得呢？', undefined, 'onboarding.portrait')
  }

  const regeneratePortrait = async () => {
    setBusy(true)
    setHint(null)
    setPortraitPanelHint(null)

    // Clear the clip catalog only after the new portrait lands — an eager
    // clear on a transient failure would leave Tier 2/3 clips gone.
    // avatar.regenerate returns {queued, job_id}; the real result arrives
    // via the avatar.regenerated event — await it before swapping.
    try {
      const queued = await requestGateway<{ queued?: boolean; job_id?: string; asset_url?: string }>(
        'avatar.regenerate',
        { feedback: undefined }
      )

      if (queued?.asset_url) {
        setPortraitUrl(queued.asset_url)
        clearClipCatalog()
        void speak('换一个样子，这样如何？', undefined, 'onboarding.portrait.regenerate')
      } else if (queued?.queued && queued.job_id) {
        const result = await awaitAvatarRegeneration(queued.job_id)

        if (result.asset_url) {
          setPortraitUrl(result.asset_url)
          clearClipCatalog()
          void speak('换一个样子，这样如何？', undefined, 'onboarding.portrait.regenerate')
        } else {
          // Failure path — keep the existing clip catalog intact so the
          // user can keep their Tier 2/3 clips while the regenerate
          // is retried.
          setPortraitPanelHint(result.error ?? '暂时换不出来，稍后再试吧')
          setHint(result.error ?? '暂时换不出来，稍后再试吧')
        }
      } else {
        setPortraitPanelHint('暂时换不出来，稍后再试吧')
        setHint('暂时换不出来，稍后再试吧')
      }
    } catch {
      // Surface the failure on the portrait panel too — the form hint is hidden behind it.
      setPortraitPanelHint('暂时换不出来，稍后再试吧')
      setHint('暂时换不出来，稍后再试吧')
    } finally {
      setBusy(false)
    }
  }

  const uploadPortrait = async () => {
    try {
      const [path] = await window.deskagent.selectPaths({
        title: '选择一张图片作为基准形象',
        filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }]
      })

      if (!path) {
        return
      }

      const dataUrl = await window.deskagent.readFileDataUrl(path)
      const comma = dataUrl.indexOf(',')
      const mime = comma > 0 ? dataUrl.slice(5, comma) : 'image/png'
      const base64 = comma > 0 ? dataUrl.slice(comma + 1) : ''

      if (!base64) {
        return
      }

      setPickedImage({ base64, contentType: mime, previewUrl: dataUrl })
      setRefineDescription('')
      setPortraitPanelHint(null)
    } catch {
      setHint('选择图片失败了，换个方式试试？')
    }
  }

  const confirmPickedImage = async () => {
    if (!pickedImage) {
      return
    }

    setBusy(true)
    setPortraitPanelHint(null)

    try {
      // POST base64 JSON — the desktop REST IPC speaks JSON, not multipart.
      const res = await window.deskagent.api<{ asset_url?: string }>({
        path: '/api/companion/avatar/upload',
        method: 'POST',
        body: { image: pickedImage.base64, content_type: pickedImage.contentType }
      })

      if (res?.asset_url) {
        clearClipCatalog()
        setPortraitUrl(res.asset_url)
        setPickedImage(null)
        setRefineDescription('')
        void speak('用你给的样子，这样如何？', undefined, 'onboarding.portrait.upload')
      }
    } catch {
      setPortraitPanelHint('上传失败了，换张图试试？')
    } finally {
      setBusy(false)
    }
  }

  const refinePickedImage = async () => {
    if (!pickedImage) {
      return
    }

    setBusy(true)
    setPortraitPanelHint(null)

    try {
      // The upload may not meet the portrait seed contract (busy background,
      // off-character framing) — the backend re-renders it from the image as a
      // subject reference, with the description folded in as an adjustment.
      const res = await window.deskagent.api<{ asset_url?: string }>({
        path: '/api/companion/avatar/from-image',
        method: 'POST',
        body: {
          content_type: pickedImage.contentType,
          description: refineDescription.trim() || undefined,
          image: pickedImage.base64
        }
      })

      if (res?.asset_url) {
        clearClipCatalog()
        setPortraitUrl(res.asset_url)
        setPickedImage(null)
        setRefineDescription('')
        void speak('按你给的参考重新画好了，这样如何？', undefined, 'onboarding.portrait.fromimage')
      }
    } catch {
      setPortraitPanelHint('按参考重绘失败了，稍后再试吧')
    } finally {
      setBusy(false)
    }
  }

  const cancelPickedImage = () => {
    setPickedImage(null)
    setRefineDescription('')
    setPortraitPanelHint(null)
  }

  const confirmPortrait = async () => {
    // Show the backend's ranked alternatives alongside the full ZH catalog; the matched voice is the default.
    const matched = await matchVoicePreference(requestGateway, answers.voice ?? '')
    setVoice(matched.voice)
    setCompanionVoiceId(matched.voice.id)
    setPhase('voice')
    // Force the ZH tab on initial entry — even if a previous session left
    // voiceLangFilter='en' in storage, the new user gets the curation
    // they signed up for. Pick the new voice id as the catalogue start
    // BEFORE the network fetch so the catalog refresh picks the right
    // default voice on the first paint.
    setVoiceLangFilter('zh')
    const catalog = await fetchVoiceCatalog(requestGateway, 'zh')
    // Lead with the closest matches so the user can browse without scrolling the full catalog.
    setVoiceCatalog([matched.voice, ...matched.alternatives, ...catalog.voices.filter(v => v.id !== matched.voice.id)])
    void speak(sampleLine(answers.name || ''), matched.voice.id || undefined, 'onboarding.voice.preview')
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
      void speak('还差一点点，咱们再确认一次。', undefined, 'onboarding.finishing.retry')

      return
    }

    setPhase('greeting')

    // Play a 3s greeting transition so the companion visibly hatches (Tier 1 fallback if no clip).
    playTransitionClip('greeting', 3000)

    const ok = await speak(
      `您好，我是${answers.name?.trim() || '您的伙伴'}。很高兴见到您！`,
      undefined,
      'onboarding.greeting'
    )

    if (!ok) {
      setHint('（声音暂时不可用）')
    }

    await sleep(ok ? 600 : 1800)
    onCompleted()
  }

  const clarity = phase === 'q' ? (qIndex + (input ? 0.5 : 0)) / QUESTIONS.length : 1
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
        <Silhouette clarity={clarity} size={160} spin={phase === 'hatching'} />

        <div
          className="w-full rounded-2xl border border-white/10 bg-black/45 p-5 text-white shadow-2xl backdrop-blur-md"
          style={{ pointerEvents: 'auto' }}
        >
          {voicePreparing && <p className="mb-2 text-center text-[10px] text-white/40">🔊 正在准备声音…</p>}
          {phase === 'q' && (
            <>
              <p className="min-h-[3.5rem] text-[15px] leading-relaxed">{spokenText}</p>
              {presetValues.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {presetValues.map(p => (
                    <button
                      className="rounded-full border border-white/20 bg-white/5 px-3 py-1 text-xs transition hover:bg-white/15"
                      key={p}
                      onClick={() => setInput(p)}
                      type="button"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              )}
              {question.multiline ? (
                <textarea
                  className="mt-3 w-full resize-none rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm outline-none placeholder:text-white/40 focus:border-white/40"
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
                  placeholder={question.placeholder}
                  ref={inputRef}
                  value={input}
                />
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
                    {qIndex === QUESTIONS.length - 1 ? '完成' : '发送'}
                  </button>
                </div>
              </div>
              {hint && <p className="mt-2 text-xs text-amber-300/80">{hint}</p>}
              <p className="mt-2 text-right text-[10px] text-white/30">
                {qIndex + 1} / {QUESTIONS.length}
              </p>
            </>
          )}

          {phase === 'hatching' && (
            <p className="py-6 text-center text-sm text-white/80">{hint || '让我想想我该是什么样子…'}</p>
          )}

          {(phase === 'voice' || phase === 'greeting' || (phase === 'portrait' && !pickedImage)) && (
            <PortraitPanel hint={portraitPanelHint} name={answers.name?.trim() || '伙伴'} url={portraitUrl} />
          )}

          {phase === 'portrait' && pickedImage && (
            <div className="mt-4">
              <div className="flex justify-center">
                <img
                  alt={answers.name?.trim() || '伙伴'}
                  className="h-40 w-40 rounded-xl object-cover shadow-lg"
                  src={pickedImage.previewUrl}
                />
              </div>
              <textarea
                className="mt-3 w-full resize-none rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-xs outline-none placeholder:text-white/40 focus:border-white/40"
                disabled={busy}
                onChange={e => setRefineDescription(e.target.value.slice(0, MAX_APPEARANCE))}
                placeholder="可补充描述，比如：背景要纯白、头发换成黑色…（可留空）"
                rows={2}
                value={refineDescription}
              />
              <div className="mt-3 flex items-center justify-between text-xs">
                <div className="flex gap-3">
                  <button
                    className="text-white/70 transition hover:text-white disabled:opacity-40"
                    disabled={busy}
                    onClick={() => void confirmPickedImage()}
                    type="button"
                  >
                    {busy ? '处理中…' : '就用这张'}
                  </button>
                  <button
                    className="text-white/70 transition hover:text-white disabled:opacity-40"
                    disabled={busy}
                    onClick={() => void refinePickedImage()}
                    type="button"
                  >
                    {busy ? '重绘中…' : '以它为基准重绘'}
                  </button>
                  <button
                    className="text-white/40 transition hover:text-white disabled:opacity-40"
                    disabled={busy}
                    onClick={cancelPickedImage}
                    type="button"
                  >
                    取消
                  </button>
                </div>
              </div>
              {portraitPanelHint && <p className="mt-2 text-xs text-rose-300/90">{portraitPanelHint}</p>}
            </div>
          )}

          {phase === 'portrait' && !pickedImage && (
            <div className="mt-4 flex items-center justify-between text-xs">
              <div className="flex gap-3">
                <button
                  className="text-white/70 transition hover:text-white disabled:opacity-40"
                  disabled={busy}
                  onClick={regeneratePortrait}
                  type="button"
                >
                  {busy ? '生成中…' : '重新生成'}
                </button>
                <button
                  className="text-white/70 transition hover:text-white disabled:opacity-40"
                  disabled={busy}
                  onClick={uploadPortrait}
                  type="button"
                >
                  自己上传
                </button>
              </div>
              <button
                className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white"
                onClick={confirmPortrait}
                type="button"
              >
                就这样吧
              </button>
            </div>
          )}

          {phase === 'voice' && voice && (
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

function PortraitPanel({ url, name, hint }: { url: string | null; name: string; hint: string | null }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="flex justify-center">
        {url ? (
          <img alt={name} className="h-40 w-40 rounded-xl object-cover shadow-lg" src={url} />
        ) : (
          <div className="grid h-40 w-40 place-items-center rounded-xl bg-white/5 text-center text-xs text-white/50">
            {name}
          </div>
        )}
      </div>
      {/* Regenerate failures surface on the portrait panel itself. */}
      {hint && <p className="text-xs text-rose-300/90">{hint}</p>}
    </div>
  )
}
