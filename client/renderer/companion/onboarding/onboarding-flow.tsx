import { useStore } from '@nanostores/react'
import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  clearDraftRefImage,
  loadDraftRefImage,
  pickAvatarImage,
  type PickedImage,
  resolvePortraitUrl,
  saveDraftRefImage
} from '@/companion/avatar-image'
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
import { portraitIntroHint } from '@/companion/portrait-flow-copy'
import {
  $activeAvatarId,
  $portraitHistory,
  $portraitSelectedIdx,
  $portraitUrl,
  $regenFeedback,
  applyPortrait,
  clearPortraitHistory,
  clearRegenFeedback,
  hydratePortraitHistory,
  type PortraitEntry,
  pushPortraitEntry,
  selectAvatar,
  selectPortraitEntry,
  setActiveAvatarId,
  setRegenFeedback
} from '@/companion/portrait-store'
import { useRegeneratePortrait } from '@/companion/use-regenerate-portrait'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { isClientErrorIpc, unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { safeJsonParse } from '@/shared/lib/safe-json'
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
import { speakScripted, stopSpeaking } from '../tts'
import { fetchVoiceCatalogRaw, matchVoicePreference, nextVoice, sampleLine, type VoiceOption } from '../voice'
import { $voicePreparing } from '../voice-state'

import { computeBackTransition } from './back-transition'
import { type OnboardingAudioTag, playOnboardingAudio } from './onboarding-audio'
import { Chip, HistoryGallery, type HistoryGalleryItem, PortraitLightbox, PortraitPanel } from './onboarding-components'

type Phase =
  | 'q-character'
  | 'hatching'
  | 'portrait-avatar'
  | 'fullbody-3d'
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

export interface FullbodyStyleOption {
  id: string
  label_zh: string
  description_zh?: string
}

type QKey = keyof OnboardingAnswers

// 这个 chip 用来挑「用户接下来要填的是哪一类答案」，本身不是答案——见 CALL_NAME_KINDS。
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
  // Manifest tag 与录到的语音行绑定，而不是与位置绑定——重排 QUESTIONS 也不能让音频错位。
  audioTag: OnboardingAudioTag
  presets?: readonly string[]
  max?: number
  // 允许用户在文本答案之外再附带一张参考图。
  allowImage?: boolean
  // 与 `presets` 互斥：双层入口，而不是「点 chip 就把输入框填好」。
  kinds?: readonly AnswerKind[]
}

// "名字 / 昵称" 是称呼的类别，不是称呼本身——把 chip 的字面文本写进输入框会
// 把「昵称」存成怎么称呼用户。点 chip 只是给输入框换个标签，再去问具体的值；
// 「称号」再额外给几个现成选项，因为它本身就是答案。
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
    multiline: false,
    audioTag: 'onboarding.q0'
  },
  {
    key: 'species',
    text: '那我是哪种生灵呢？',
    placeholder: '如：精灵、人类、龙…（可直接输入或选择标签）',
    required: true,
    multiline: false,
    audioTag: 'onboarding.q1',
    presets: SPECIES_PRESETS
  },
  {
    key: 'character_gender',
    text: '嗯…那我是男性、女性、还是…',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q2',
    presets: CHARACTER_GENDER_PRESETS
  },
  {
    // appearance_core：锁定的视觉锚点——既会喂给 3D 模型 prompt，
    // 又会在用户确认头像图后从 PUT /persona 中被剔除。标签旁的红色 `*`
    key: 'appearance_core',
    text: '您希望我长什么样？说说头发、眼睛、体型、标志性细节…',
    placeholder: '比如：金发绿眼、额间一道疤、机械义眼…',
    required: false,
    multiline: true,
    audioTag: 'onboarding.q3',
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
    audioTag: 'onboarding.q4',
    presets: ROLE_PRESETS
  },
  {
    key: 'personality',
    text: '您希望我是什么性格？',
    placeholder: '自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q5',
    presets: PERSONALITY_PRESETS
  },
  // speaking_style 是后端 schema 的必填项——用专门一道题去问，
  // 让用户的选择成为直接真相来源；它属于角色字段，跟其它字段一起进 enterHatching 的 PUT。
  {
    key: 'speaking_style',
    text: '您希望我说话的风格是什么样的？',
    placeholder: '比如：简短、爱用比喻、俏皮一点…',
    required: true,
    multiline: true,
    audioTag: 'onboarding.q10',
    max: 500,
    presets: SPEAKING_STYLE_PRESETS
  },
  {
    key: 'voice',
    text: '您希望我听起来是什么样的？比如温柔的少女音、沉稳的男声、活泼的正太…',
    placeholder: '描述你想要的声音…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q12',
    presets: VOICE_PRESETS
  },
  {
    key: 'user_call_name',
    text: '我该怎么称呼您？',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q6',
    max: MAX_USER_TEXT,
    kinds: CALL_NAME_KINDS
  },
  {
    key: 'user_gender',
    text: '您方便告诉我您的性别吗？',
    placeholder: '或自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q7',
    max: MAX_USER_TEXT,
    presets: USER_GENDER_PRESETS
  },
  {
    key: 'user_age_bucket',
    text: '您属于哪个年龄段？',
    placeholder: '或自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q8',
    max: MAX_USER_TEXT,
    presets: USER_AGE_BUCKET_PRESETS
  },
  {
    key: 'user_hobbies',
    text: '您平时喜欢什么？',
    placeholder: '可以多写几个…',
    required: false,
    multiline: true,
    audioTag: 'onboarding.q9',
    max: MAX_USER_TEXT
  },
  {
    key: 'user_freeform',
    text: '还有什么想告诉我、或者想叮嘱我的吗？',
    placeholder: '可跳过…',
    required: false,
    multiline: true,
    audioTag: 'onboarding.q11',
    max: MAX_USER_TEXT
  }
]

// 这些字段的值会驱动 3D 模型，因此用户在确认头像后不能再改。
// 题面旁边会渲染一个红色 `*`，向导顶部还有 banner 提示用户这一限制。
const LOCKED_FIELD_KEYS: ReadonlySet<QKey> = new Set(['species', 'character_gender', 'appearance_core'])

// 分段边界由 ``voice`` 这道题的位置决定——之前全是角色子阶段，
// 它本身是声音子阶段，之后都是用户子阶段。对应后端 ONBOARDING_FIELDS 的顺序。
const _VOICE_Q_INDEX = QUESTIONS.findIndex(q => q.key === 'voice')
const CHARACTER_QUESTIONS: readonly Question[] = QUESTIONS.slice(0, _VOICE_Q_INDEX)
const VOICE_QUESTIONS: readonly Question[] = QUESTIONS.slice(_VOICE_Q_INDEX, _VOICE_Q_INDEX + 1)
const USER_QUESTIONS: readonly Question[] = QUESTIONS.slice(_VOICE_Q_INDEX + 1)

const PHASE_QUESTIONS: Record<Phase, readonly Question[]> = {
  'q-character': CHARACTER_QUESTIONS,
  'q-user': USER_QUESTIONS,
  voice: VOICE_QUESTIONS,
  hatching: [],
  'portrait-avatar': [],
  'fullbody-3d': [],
  finishing: [],
  greeting: []
}

// 把 resume 的 next_field 路由到 q-user；`voice` 有自己的分支。
// 从 USER_QUESTIONS 推导，题目增删时自动保持同步。
const POST_CHARACTER_FIELDS: ReadonlySet<string> = new Set(USER_QUESTIONS.map(q => q.key))

// 提到顶层：否则 useInteractiveRegion 的 effect 会在每次渲染时重新注册。
const interactiveRegionRect = (el: HTMLElement): DOMRect | null => {
  const rect = el.getBoundingClientRect()

  return rect.width === 0 || rect.height === 0 ? null : rect
}

// `fn` 抛出的错误会向上传播，方便调用方把 4xx 重新抛出并提前结束重试。
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

// 可经 onboarding.submit 提交的 question key——与后端 ONBOARDING_FIELDS 对齐。
// 映射都是恒等的（question key === 后端字段名），所以用 Set 就够了。
// appearance_outfit 不在其中：它是 Persona 字段，由 persona-editor / persona-retune 编辑，不在 onboarding 里收集。
const ONBOARDING_FIELD_KEYS: ReadonlySet<QKey> = new Set<QKey>([
  'name',
  'species',
  'character_gender',
  'appearance_core',
  'role',
  'personality',
  'speaking_style',
  'user_call_name',
  'user_gender',
  'user_age_bucket',
  'user_hobbies',
  'user_freeform',
  'voice'
])

// 头像（半身像）生成。返回后端的原始响应；解析步骤由 applyPortrait 负责。
async function generatePortrait(reference: PickedImage | null): Promise<{
  asset_url?: string
  id?: number
} | null> {
  try {
    const res = await window.spiritagent.api<{
      asset_url?: string
      id?: number
    }>({
      path: reference ? '/api/companion/avatar/from-image' : '/api/companion/avatar',
      method: 'POST',
      body: reference ? { content_type: reference.contentType, image: reference.base64 } : {}
    })

    return res
  } catch (error) {
    // 把确定性的失败重抛出去，避免 retryTransient 烧掉 120 秒的头像预算。
    if (isClientErrorIpc(error)) {
      throw error
    }

    return null
  }
}

async function savePersona(payload: ReturnType<typeof assemblePersona>): Promise<boolean> {
  try {
    await window.spiritagent.api({
      path: '/api/companion/persona',
      method: 'PUT',
      body: { definition_json: JSON.stringify(payload) }
    })

    return true
  } catch (error) {
    // 重抛 4xx，避免 retryTransient 在确定性失败上空耗重试。
    if (isClientErrorIpc(error)) {
      throw error
    }

    return false
  }
}

interface OnboardingFlowProps {
  onCompleted: () => void
}

// 放到 OnboardingFlow 外面：否则它订阅的 $regenFeedback 会在每次按键时让整个对话框重渲染。
function RegenFeedbackInput(): React.JSX.Element {
  const value = useStore($regenFeedback)

  return (
    <textarea
      className={`${INPUT_CLASS} text-xs`}
      maxLength={MAX_APPEARANCE}
      onChange={e => setRegenFeedback(e.target.value)}
      placeholder="哪里不满意？比如：头发再短一点、眼睛再大一点、表情更温和…（可留空直接重新生成）"
      rows={2}
      value={value}
    />
  )
}

function SpinnerWithText({ text, size = 'h-5 w-5' }: { text: string; size?: string }): React.JSX.Element {
  return (
    <div className="flex flex-col items-center gap-2 py-4">
      <div className={`${size} animate-spin rounded-full border-2 border-white/30 border-t-white/80`} />
      <p className="text-sm text-white/70">{text}</p>
    </div>
  )
}

export function OnboardingFlow({ onCompleted }: OnboardingFlowProps): React.JSX.Element | null {
  const gatewayState = useStore($gatewayState)
  const voicePreparing = useStore($voicePreparing)
  const { requestGateway } = useGatewayRequest()
  const [phase, setPhase] = useState<Phase>('q-character')
  // confirm-front 成功后置 true:形象已锁死 + 3D 已启动,任何返回到 portrait-avatar / fullbody-3d 的路径都禁用
  const [imageSealed, setImageSealed] = useState(false)
  const [qIndex, setQIndex] = useState(0)
  const onboardingSubmissionsRef = useRef(Promise.resolve())
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  // 当前头像行 id 由 applyPortrait 写入全局 $activeAvatarId atom——
  // 这里订阅它，让重生结果自动传播，省得我们在每个调用点都手写 setState。
  const activeAvatarId = useStore($activeAvatarId)
  // 历史画廊——头像面板下方的缩略图。
  const portraitHistory = useStore($portraitHistory)
  const portraitSelectedIdx = useStore($portraitSelectedIdx)
  // voice 阶段先跑 Q7 描述输入，再进入目录选择器。
  const [voiceStage, setVoiceStage] = useState<VoiceStage>('describe')

  // 失败时保留当前头像：它已经持有解析好的字节。
  // 共用的 `applyPortrait` 负责写入全局 $portraitUrl 与 $activeAvatarId atom。
  const applyLocalPortrait = async (
    response:
      | {
          asset_url?: string | null
          id?: number
        }
      | null
      | undefined
  ): Promise<{ avatar: string | null; id: number | null }> => {
    const { avatar } = await applyPortrait({
      id: response?.id,
      assetUrl: response?.asset_url
    })

    if (avatar) {
      setPortraitUrl(avatar)
    }

    return { avatar, id: response?.id ?? null }
  }

  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  // 匹配器的候选项。跟完整目录分开，方便「推荐卡」的「换一个」按钮在候选项里循环，
  // 而不是遍历整个目录。
  const [voiceAlternatives, setVoiceAlternatives] = useState<VoiceOption[]>([])
  const [voiceLangFilter, setVoiceLangFilter] = useState<VoiceLanguageFilter>('zh')
  // 失败提示挂在头像面板上——表单区被它压在下面。
  const [portraitPanelHint, setPortraitPanelHint] = useState<string | null>(null)

  const [styleCatalog, setStyleCatalog] = useState<FullbodyStyleOption[]>([])
  const [fullbodyLoading, setFullbodyLoading] = useState(false)
  const [fullbodyLoadingText, setFullbodyLoadingText] = useState('正在为您生成不同风格的全身样图…')
  const [fullbodySamples, setFullbodySamplesState] = useState<Record<string, string>>({})
  const [fullbodyRawSamples, setFullbodyRawSamplesState] = useState<Record<string, string>>({})
  const [fullbodyStyle, setFullbodyStyleState] = useState<string | null>(null)
  const [selectedStyleKey, setSelectedStyleKey] = useState<string>('')
  const [fullbodyFrontUrl, setFullbodyFrontUrl] = useState<string | null>(null)
  const [fullbodyFrontRawUrl, setFullbodyFrontRawUrl] = useState<string | null>(null)
  const [fullbodyFeedback, setFullbodyFeedback] = useState<string>('')
  const [fullbodyHint, setFullbodyHint] = useState<string | null>(null)
  const [fullbodyZoomUrl, setFullbodyZoomUrl] = useState<string | null>(null)

  const [fullbodyHistories, setFullbodyHistories] = useState<
    Record<string, Array<{ rawUrl: string | null; previewUrl: string }>>
  >({})

  const [fullbodyHistoryIndices, setFullbodyHistoryIndices] = useState<Record<string, number>>({})

  // 在「形象描述」题目提交上来的参考图。本地用 IndexedDB 草稿缓存持久化，
  // 这样半身生成前崩溃后重启也能带回来。
  const [refImage, setRefImage] = useState<PickedImage | null>(null)

  // 头像重生时挑的展现/画风参考图——跟 Q4 的身份图共存，而不是替换它。
  // 仅存内存：是临时性的重生辅助，不是持久化的身份资产。
  const [presentationRef, setPresentationRef] = useState<PickedImage | null>(null)

  const updateRefImage = (img: PickedImage | null) => {
    setRefImage(img)
    void saveDraftRefImage(img)
  }

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

  // 居中的初始位置；用户可以从这里开始拖拽。
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

  // Onboarding 对话框完全可交互——把它的实际可见矩形注册到全局 interactive-regions 注册表，
  // SpriteStage 的命中测试就只在光标停在对话框表单卡片上时才捕获。卸载时 SpriteStage 会恢复穿透。
  useInteractiveRegion('onboarding', containerRef, interactiveRegionRect)

  useEffect(() => {
    return () => {
      stopSpeaking()
    }
  }, [])

  // 反馈 textarea 在多个头像面板之间共享。阶段切换时清空，
  // 避免 onboarding 里输入的「头发再短一点」泄漏到后续重生流程里。
  useEffect(() => {
    if (phase === 'portrait-avatar') {
      clearRegenFeedback()
    }
  }, [phase])

  // 拖拽用 document 级监听器（而不是容器上的 React onPointerMove），
  // 这样光标离开对话框矩形后拖拽仍能继续，并能在悬停在别的区域时持续更新位置。
  // setPointerCapture 会干扰表单上按钮/输入框触发的 click 事件，所以不用它。
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
      // 拖拽过程中指针离开窗口要清掉拖拽状态，避免后续 move 拿旧的原点坐标去算位移。
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

  const currentList = PHASE_QUESTIONS[phase]

  const question = currentList[qIndex]
  // 用 ref 持有最新 answers，让 speak/focus effect 只在 phase/qIndex 变化时重跑，
  // 而不是每次按键都跑（exhaustive-deps lint 看不到这个意图）。
  const answersRef = useLatestRef(answers)

  // 题面文本，显示在输入框下方。
  const spokenText = question?.text ?? ''

  // 每道题出现时朗读（默认中性声；plan §3.2）。
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

    void playOnboardingAudio(q.audioTag)

    return () => stopSpeaking()
  }, [phase, qIndex, currentList, answersRef])

  const submitOnboardingAnswer = useCallback(
    (field: QKey, value: string | null) => {
      const submission = onboardingSubmissionsRef.current
        .then(async () => {
          await requestGateway('onboarding.submit', { field, value })
        })
        .catch(() => undefined)

      onboardingSubmissionsRef.current = submission

      return submission
    },
    [requestGateway]
  )

  useEffect(() => {
    const isQuestionPhase = phase === 'q-character' || phase === 'q-user' || phase === 'voice'

    if (isQuestionPhase && currentList[qIndex]) {
      ;(currentList[qIndex].multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, qIndex, currentList])

  const commit = (value: string | undefined): OnboardingAnswers => {
    const q = currentList[qIndex]

    if (!q) {
      return answers
    }

    const trimmed = value && value.trim() ? value.trim() : undefined
    const cleaned = trimmed && q.max ? trimmed.slice(0, q.max) : trimmed
    const nextAnswers: OnboardingAnswers = { ...answers, [q.key]: cleaned }
    setAnswers(nextAnswers)

    // 逐字段增量持久化（design §7.5）；fire-and-forget——绝不阻塞 UI 等草稿保存。
    // 网关未打开前是空操作。
    if (gatewayState === 'open' && ONBOARDING_FIELD_KEYS.has(q.key)) {
      void submitOnboardingAnswer(q.key, cleaned ?? null)
    }

    return nextAnswers
  }

  const advance = (updatedAnswers?: OnboardingAnswers) => {
    const currentAnswers = updatedAnswers ?? answers

    // Voice describe 只有一道题；点下一题会切到 catalog，由下面的 useEffect 加载。
    if (phase === 'voice' && voiceStage === 'describe') {
      setVoiceStage('catalog')

      return
    }

    if (qIndex < currentList.length - 1) {
      setQIndex(qIndex + 1)

      return
    }

    if (phase === 'q-character') {
      void enterHatching(currentAnswers)
    } else if (phase === 'q-user') {
      setPhase('finishing')
      void finish(currentAnswers)
    }
  }

  // 在 describe→catalog 切换（以及 resume 直接进入 catalog）时加载目录与试听 TTS。
  useEffect(() => {
    if (phase !== 'voice' || voiceStage !== 'catalog') {
      return
    }
    void (async () => {
      stopSpeaking()
      const matched = await matchVoicePreference(requestGateway, answers.voice ?? '')
      setVoice(matched.voice)
      setVoiceAlternatives(matched.alternatives)
      setCompanionVoiceId(matched.voice.id)
      setVoiceLangFilter('zh')
      const r = await fetchVoiceCatalogRaw(requestGateway, 'zh')
      // 同时把 matched voice 与它的 alternatives 都剔除——它们已经被前置了。
      // 不剔除的话，同时也在目录里的 alternative（如「茉莉」）会重复出现，
      // 每次切换声音时列表都能看出重了。
      const priorityIds = new Set([matched.voice.id, ...matched.alternatives.map(v => v.id)])
      const extra = r.ok ? r.catalog.voices.filter(v => !priorityIds.has(v.id)) : []
      setVoiceCatalog([matched.voice, ...matched.alternatives, ...extra])
      void speakScripted(sampleLine(answers.name || ''), matched.voice.id || undefined, 'onboarding.voice.preview')
    })()
  }, [phase, voiceStage, requestGateway, answers.voice, answers.name])

  const onSend = () => {
    const q = currentList[qIndex]

    if (q?.required && !input.trim()) {
      const requiredHints: Record<string, string> = {
        name: '名字是必填的哦～',
        species: '生灵类型是必填的哦～',
        speaking_style: '说话风格是必填的哦～'
      }

      setHint(requiredHints[q.key] ?? '此项是必填的哦～')

      return
    }

    const nextAnswers = commit(input)
    advance(nextAnswers)
  }

  const onSkip = () => {
    if (question?.required) {
      return
    }

    const nextAnswers = commit(undefined)
    advance(nextAnswers)
  }

  const onBack = () => {
    // 形象确认后 3D 已启动,任何返回路径都禁用——纯函数 ``computeBackTransition`` 在 imageSealed 时直接返 null。
    const intent = computeBackTransition({ phase, qIndex, voiceStage, imageSealed }, CHARACTER_QUESTIONS.length)

    if (!intent) {
      return
    }

    if (intent.phase !== phase) {
      setPhase(intent.phase)
    }

    if (intent.qIndex !== undefined && intent.qIndex !== qIndex) {
      setQIndex(intent.qIndex)
    }

    if (intent.voiceStage !== undefined && intent.voiceStage !== voiceStage) {
      setVoiceStage(intent.voiceStage)
    }
  }

  const enterHatching = async (currentAnswers?: OnboardingAnswers, imageOverride?: PickedImage | null) => {
    // 形象已锁死时不应再进入头像/全身图阶段。深度防御:onBack 守卫 + 此处显式短路,即使上游误调也无效。
    if (imageSealed) {
      return
    }

    // 只有同时有服务端行 AND 有效的头像图时才能跳过生成。
    // TTL 到期后续传时，$activeAvatarId 还在但 $portraitUrl 已经为 null——这种情况必须重新生成。
    if (activeAvatarId != null && $portraitUrl.get()) {
      setPhase('portrait-avatar')
      void playOnboardingAudio('onboarding.portrait.ok')

      return
    }

    const ans = currentAnswers ?? answers
    const img = imageOverride !== undefined ? imageOverride : refImage
    setPhase('hatching')
    setHint(null)
    void playOnboardingAudio('onboarding.hatching')

    // 先固化 persona 再做头像——avatar 生成需要 is_complete=true；user_* 之后由 submit_onboarding_field 路由到 Memory。
    // savePersona 把 4xx 重新抛出；退回表单让用户修改该字段。
    let personaOk = false
    await onboardingSubmissionsRef.current

    try {
      personaOk = (await retryTransient(() => savePersona(assembleCharacterPersona(ans)), 700)) === true
    } catch (err) {
      setPhase('q-character')
      setHint(err instanceof Error ? `记忆存不上：${err.message}` : '记忆存不上，请重试 onboarding')
      void playOnboardingAudio('onboarding.hatching.retry')

      return
    }

    let url: string | null = null

    if (personaOk) {
      try {
        const applied = await applyLocalPortrait(await retryTransient(() => generatePortrait(img), 1500, 2))
        url = applied.avatar

        if (url) {
          pushPortraitEntry({
            portraitUrl: url,
            avatarId: applied.id ?? activeAvatarId
          })
        }
      } catch {
        // 确定性的 4xx（参考图不可用、persona 不完整）不能让流程卡在 'hatching'——
        // 直接落到 portrait 阶段，那里仍然支持带反馈或不带反馈的重生。
        url = null
      }

      if (!url) {
        // 接下来渲染的是 portrait 面板；`hint` 只在表单里能看到。
        setPortraitPanelHint(img ? '这张参考图我没能用上…待会儿再换一张吧' : '我还没想好…')
      }
    } else {
      setHint('记忆还没存好，稍后再试试形象吧…')
    }

    // avatar 阶段：用户审视头像并确认——确认即锁定形象，并解锁 voice 子阶段。
    setPhase('portrait-avatar')
    void playOnboardingAudio(url ? 'onboarding.portrait.ok' : 'onboarding.portrait.failed')
  }

  const enterHatchingRef = useLatestRef(enterHatching)

  // 从 avatar 行复水 fullbody 阶段——持久化的风格样图、已选风格和正面种子——
  // 让重启能从上次中断处继续，且绝不重新触发付费样图生成。
  // 仅在没有存储内容（首次进入 / 旧版行）或 temp-media 草稿过了 TTL 时才回退到重新生成。
  const hydrateFullbodyStage = async (): Promise<void> => {
    void window.spiritagent
      .api<FullbodyStyleOption[]>({
        path: '/api/companion/avatar/fullbody/styles',
        method: 'GET'
      })
      .then(res => {
        if (Array.isArray(res) && res.length > 0) {
          setStyleCatalog(res)
        }
      })
      .catch(() => undefined)

    const avatarRes = await window.spiritagent.api<{
      asset_url?: string | null
      seed_front_url?: string | null
      id?: number
      fullbody_style?: string | null
      fullbody_samples?: Record<string, string>
    }>({
      path: '/api/companion/avatar',
      method: 'GET'
    })

    await applyLocalPortrait(avatarRes)
    setPhase('fullbody-3d')

    const rawSamples = avatarRes?.fullbody_samples ?? {}
    let resolvedSamples: Record<string, string> = {}

    if (Object.keys(rawSamples).length > 0) {
      resolvedSamples = {}

      for (const [styleId, rawUrl] of Object.entries(rawSamples)) {
        const dataUrl = await resolvePortraitUrl(rawUrl)

        if (dataUrl) {
          resolvedSamples[styleId] = dataUrl
        }
      }

      setFullbodyRawSamplesState(rawSamples)
      setFullbodySamplesState(resolvedSamples)
    }

    const style = avatarRes?.fullbody_style || null
    setFullbodyStyleState(style)

    if (style) {
      setSelectedStyleKey(style)
    } else {
      const firstAvailableKey = Object.keys(rawSamples)[0]

      if (firstAvailableKey) {
        setSelectedStyleKey(prev => prev || firstAvailableKey)
      }
    }

    let initialFrontRaw: string | null = null
    let initialFrontResolved: string | null = null

    if (avatarRes?.seed_front_url) {
      initialFrontRaw = avatarRes.seed_front_url
      initialFrontResolved = await resolvePortraitUrl(avatarRes.seed_front_url)
    } else if (style && rawSamples[style]) {
      initialFrontRaw = rawSamples[style]
      initialFrontResolved = resolvedSamples[style] ?? null
    }

    setFullbodyFrontRawUrl(initialFrontRaw)
    setFullbodyFrontUrl(initialFrontResolved)

    const initialHistories: Record<string, Array<{ rawUrl: string | null; previewUrl: string }>> = {}
    const initialIndices: Record<string, number> = {}

    for (const [styleId, preview] of Object.entries(resolvedSamples)) {
      if (preview) {
        initialHistories[styleId] = [{ rawUrl: rawSamples[styleId] || null, previewUrl: preview }]
        initialIndices[styleId] = 0
      }
    }

    if (style && initialFrontResolved) {
      const samplePreview = resolvedSamples[style]

      if (samplePreview && initialFrontResolved !== samplePreview) {
        initialHistories[style] = [
          { rawUrl: rawSamples[style] || null, previewUrl: samplePreview },
          { rawUrl: initialFrontRaw, previewUrl: initialFrontResolved }
        ]
        initialIndices[style] = 1
      } else {
        initialHistories[style] = [{ rawUrl: initialFrontRaw, previewUrl: initialFrontResolved }]
        initialIndices[style] = 0
      }
    }

    setFullbodyHistories(initialHistories)
    setFullbodyHistoryIndices(initialIndices)

    if (avatarRes?.id != null && (Object.keys(rawSamples).length === 0 || Object.keys(resolvedSamples).length === 0)) {
      // 没有持久化样图，或者 temp-media 草稿已过 TTL——重新生成，别显示死卡。
      void fetchFullbodySamples(avatarRes.id)
    }
  }

  const hydrateFullbodyStageRef = useLatestRef(hydrateFullbodyStage)

  // 断点恢复（plan §3 / design §7.5）：网关一旦连通，
  // 就把还没答完的草稿拉回来，让 onboarding 中途崩溃/退出后能从下一道未答的题继续。
  // 只跑一次，绝不重复 resume。
  useEffect(() => {
    if (resumedRef.current || gatewayState !== 'open') {
      return
    }

    resumedRef.current = true

    void (async () => {
      try {
        const cachedRef = await loadDraftRefImage()

        if (cachedRef) {
          setRefImage(cachedRef)
        }

        let state: {
          answers?: Record<string, string>
          next_field?: string | null
          complete?: boolean
        } | null = null

        try {
          state = await window.spiritagent.api<{
            answers?: Record<string, string>
            next_field?: string | null
            complete?: boolean
          }>({
            path: '/api/companion/onboarding/state'
          })
        } catch {
          state = await requestGateway<{
            answers?: Record<string, string>
            next_field?: string | null
            complete?: boolean
          }>('onboarding.get_state', {}).catch(() => null)
        }

        if (state?.complete) {
          void clearDraftRefImage()
          onCompleted()

          return
        }

        if (state?.answers) {
          // 把服务端草稿与当前会话里已经输入的答案合并；
          // 本地非空的编辑优先，保证用户最近的意图不会丢失。
          const a = state.answers
          let merged: OnboardingAnswers = {}
          setAnswers(prev => {
            const next: OnboardingAnswers = { ...prev }

            for (const k of Object.keys(a) as (keyof OnboardingAnswers)[]) {
              if (next[k] == null || next[k] === '') {
                next[k] = a[k] as never
              }
            }

            merged = next

            return next
          })

          const nextField = state.next_field

          if (nextField === 'portrait') {
            try {
              await hydratePortraitHistory()

              const avatarRes = await window.spiritagent.api<{
                asset_url?: string | null
                id?: number
              }>({
                path: '/api/companion/avatar',
                method: 'GET'
              })

              const applied = await applyLocalPortrait(avatarRes)

              if (applied.avatar) {
                if (avatarRes?.id != null) {
                  const idx = $portraitHistory.get().findIndex(e => e.avatarId === avatarRes.id)

                  if (idx >= 0) {
                    selectPortraitEntry(idx)
                  }
                }

                setPhase('portrait-avatar')
              } else {
                void enterHatchingRef.current(merged, cachedRef)
              }
            } catch {
              void enterHatchingRef.current(merged, cachedRef)
            }
          } else if (nextField === 'fullbody') {
            try {
              await hydratePortraitHistory()
              await hydrateFullbodyStageRef.current()
            } catch {
              setPhase('fullbody-3d')
              setFullbodyHint('全身立绘恢复失败，请点击重新生成样图')
            }
          } else if (nextField === 'voice') {
            // next_field==='voice' 意味着描述句本身还没回答——落在 describe 上，而不是 catalog。
            setPhase('voice')
            setVoiceStage('describe')
            setQIndex(0)
          } else if (nextField && POST_CHARACTER_FIELDS.has(nextField)) {
            const idx = USER_QUESTIONS.findIndex(q => q.key === nextField)
            setPhase('q-user')
            setQIndex(Math.max(0, idx))
          } else if (nextField) {
            const idx = CHARACTER_QUESTIONS.findIndex(q => q.key === nextField)
            setPhase('q-character')
            setQIndex(Math.max(0, idx))
          }
        }
      } catch {
        /* no draft yet — start fresh */
      }

      const r = await fetchVoiceCatalogRaw(requestGateway)

      if (r.ok) {
        setVoiceCatalog(r.catalog.voices)
      }

      void window.spiritagent
        .api<FullbodyStyleOption[]>({
          path: '/api/companion/avatar/fullbody/styles',
          method: 'GET'
        })
        .then(res => {
          if (Array.isArray(res) && res.length > 0) {
            setStyleCatalog(res)
          }
        })
        .catch(() => undefined)
    })()
  }, [gatewayState, requestGateway, onCompleted, enterHatchingRef, hydrateFullbodyStageRef])

  useEffect(() => {
    if (gatewayState !== 'open' || voiceCatalog.length > 0) {
      return
    }

    void fetchVoiceCatalogRaw(requestGateway).then(r => {
      if (r.ok) {
        setVoiceCatalog(r.catalog.voices)
      }
    })
  }, [gatewayState, requestGateway, voiceCatalog.length])

  // 第一步——头像重生：新建一行 avatar，新 id 通过 hook 内 applyPortrait 自动发布到 ``$activeAvatarId``。
  const { regenerate: regenerateAvatarPortrait, busy: avatarBusy } = useRegeneratePortrait({
    refImage,
    presentationRef,
    playAudioOnSuccess: true,
    onRegenerated: ({ avatar }) => {
      if (avatar) {
        setPortraitUrl(avatar)
      }
    }
  })

  const currentHistoryItems: HistoryGalleryItem[] = useMemo(
    () => portraitHistory.map(e => ({ url: e.portraitUrl })),
    [portraitHistory]
  )

  const onSelectHistoryEntry = useCallback(
    (idx: number) => {
      const entry: PortraitEntry | undefined = portraitHistory[idx]

      if (!entry) {
        return
      }

      selectPortraitEntry(idx)

      if (entry.portraitUrl) {
        setPortraitUrl(entry.portraitUrl)
        $portraitUrl.set(entry.portraitUrl)
      }

      // 用户在画廊里点选时：当前 avatar 行必须跟显示的脸保持一致，
      // 否则选中会悄悄回退到最后一行，画面跳回用户已经拒绝过的那张脸。
      if (entry.avatarId != null) {
        setActiveAvatarId(entry.avatarId)
        void selectAvatar(entry.avatarId)
      }
    },
    [portraitHistory]
  )

  const pickReferenceImage = async () => {
    const picked = await pickAvatarImage('选择一张参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    updateRefImage(picked.image)
    setHint(null)
  }

  const pickPresentationImage = async () => {
    const picked = await pickAvatarImage('选择一张风格参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    setPresentationRef(picked.image)
    setHint(null)
  }

  const confirmPortrait = async () => {
    try {
      await window.spiritagent.api({
        path: '/api/companion/portrait/confirm',
        method: 'POST'
      })
    } catch (error) {
      // 409 表示 temp-media 已过期——头像文件已不在，绝不能继续推进。
      // 退回 avatar 阶段，让用户重新生成。
      if (isClientErrorIpc(error)) {
        const unwrapped = unwrapIpcErrorMessage(error)
        const jsonStart = unwrapped.indexOf('{')
        const parsed = jsonStart >= 0 ? safeJsonParse(unwrapped.slice(jsonStart), null) : null
        const backendError = (parsed as { detail?: { error?: string } } | null)?.detail?.error

        if (backendError) {
          setPortraitPanelHint(backendError)
          setPhase('portrait-avatar')

          return
        }
      }
      // 非 IPC 失败（网络、JSON 解析、IPC envelope）：onClick 里的 `void` 会把异常吞掉——
      // 这里显式提示并拒绝推进。

      console.warn('confirmPortrait failed unexpectedly', error)
      setPortraitPanelHint('确认失败，请检查网络后重试')

      return
    }

    clearPortraitHistory()
    setPresentationRef(null)

    // 推进到 fullbody-3d
    setPhase('fullbody-3d')
    setFullbodyStyleState(null)
    setSelectedStyleKey('')
    setFullbodyFrontUrl(null)
    setFullbodyFrontRawUrl(null)
    setFullbodyFeedback('')
    setFullbodyHint(null)
    setFullbodyZoomUrl(null)

    // 复用 avatar 行上已经持久化的样图（用户退回 portrait 又重新确认过的情况）；
    // 只有在没有现成样图时才重新生成。
    void hydrateFullbodyStage().catch(() => {
      if (activeAvatarId) {
        void fetchFullbodySamples(activeAvatarId)
      }
    })
  }

  const fetchFullbodySamples = async (avatarId: number) => {
    setFullbodyLoading(true)
    setFullbodyLoadingText('正在为您生成不同风格的全身样图…')
    setFullbodyHint(null)

    try {
      const res = await window.spiritagent.api<{ samples?: Record<string, string> }>({
        path: `/api/companion/avatar/${avatarId}/fullbody/samples`,
        method: 'POST',
        body: refImage
          ? {
              image: refImage.base64,
              content_type: refImage.contentType
            }
          : undefined
      })

      if (res?.samples && Object.keys(res.samples).length > 0) {
        setFullbodyRawSamplesState(res.samples)
        const resolved: Record<string, string> = {}

        for (const [styleId, rawUrl] of Object.entries(res.samples)) {
          const dataUrl = await resolvePortraitUrl(rawUrl)

          if (dataUrl) {
            resolved[styleId] = dataUrl
          }
        }

        setFullbodySamplesState(resolved)

        const initialHistories: Record<string, Array<{ rawUrl: string | null; previewUrl: string }>> = {}
        const initialIndices: Record<string, number> = {}

        for (const [styleId, preview] of Object.entries(resolved)) {
          initialHistories[styleId] = [{ rawUrl: res.samples[styleId] || null, previewUrl: preview }]
          initialIndices[styleId] = 0
        }

        setFullbodyHistories(initialHistories)
        setFullbodyHistoryIndices(initialIndices)

        const resolvedKeys = Object.keys(resolved)

        if (resolvedKeys.length > 0) {
          setSelectedStyleKey(prev => prev || resolvedKeys[0])
        }

        if (Object.keys(resolved).length === 0) {
          setFullbodyHint('风格样图加载失败，请重试')
        }
      } else {
        setFullbodyHint('风格样图生成未返回内容，请重试')
      }
    } catch (err) {
      setFullbodyHint(err instanceof Error ? err.message : '样图生成失败，请重试')
    } finally {
      setFullbodyLoading(false)
    }
  }

  const selectStyle = (styleId: string) => {
    setSelectedStyleKey(styleId)
    setFullbodyStyleState(styleId)

    const historyList = fullbodyHistories[styleId] || []
    const historyIdx = fullbodyHistoryIndices[styleId] ?? 0
    let frontRaw: string | null = null
    let frontResolved: string | null = null

    if (historyList.length > 0 && historyList[historyIdx]) {
      frontRaw = historyList[historyIdx].rawUrl
      frontResolved = historyList[historyIdx].previewUrl
    } else {
      frontRaw = fullbodyRawSamples[styleId] || null
      frontResolved = fullbodySamples[styleId] || null
      const sampleUrl = fullbodySamples[styleId]

      if (sampleUrl) {
        setFullbodyHistories(prev => ({
          ...prev,
          [styleId]: [{ rawUrl: frontRaw, previewUrl: sampleUrl }]
        }))
        setFullbodyHistoryIndices(prev => ({ ...prev, [styleId]: 0 }))
      }
    }

    setFullbodyFrontRawUrl(frontRaw)
    setFullbodyFrontUrl(frontResolved)
    setFullbodyHint(null)

    // 持久化本次选择，让重启能从正面预览处继续，而不是重新生成样图。
    // 尽力而为：当前会话的流程不受影响，因为 confirm-front 会显式带上正面图 URL。
    if (activeAvatarId != null) {
      void window.spiritagent
        .api({
          path: `/api/companion/avatar/${activeAvatarId}/fullbody/select-style`,
          method: 'POST',
          body: { style: styleId }
        })
        .catch(() => undefined)
    }
  }

  const onSelectFullbodyHistoryEntry = useCallback(
    (idx: number) => {
      if (!fullbodyStyle) {
        return
      }

      const list = fullbodyHistories[fullbodyStyle] || []

      if (idx >= 0 && idx < list.length) {
        const entry = list[idx]
        setFullbodyHistoryIndices(prev => ({ ...prev, [fullbodyStyle]: idx }))
        setFullbodyFrontUrl(entry.previewUrl)
        setFullbodyFrontRawUrl(entry.rawUrl)
      }
    },
    [fullbodyHistories, fullbodyStyle]
  )

  const regenerateFullbodyFront = async () => {
    if (!activeAvatarId || !fullbodyStyle) {
      return
    }

    setFullbodyLoading(true)
    setFullbodyLoadingText('正在按要求重新生成正面全身图…')
    setFullbodyHint(null)

    try {
      const res = await window.spiritagent.api<{
        id?: number
        asset_url?: string
        seed_front_url?: string
      }>({
        path: `/api/companion/avatar/${activeAvatarId}/fullbody/front`,
        method: 'POST',
        body: {
          style: fullbodyStyle,
          feedback: fullbodyFeedback.trim() || undefined,
          image: refImage?.base64,
          content_type: refImage?.contentType
        }
      })

      const applied = await applyPortrait({
        id: res?.id,
        assetUrl: res?.asset_url,
        seedFrontUrl: res?.seed_front_url
      })

      const rawFront = res?.seed_front_url || null
      let resolvedUrl: string | null = null

      if (applied.seedFront) {
        resolvedUrl = applied.seedFront
      } else if (rawFront) {
        resolvedUrl = await resolvePortraitUrl(rawFront)
      }

      if (resolvedUrl) {
        setFullbodyFrontRawUrl(rawFront)
        setFullbodyFrontUrl(resolvedUrl)

        let targetIdx = 0
        setFullbodyHistories(prev => {
          let currentList = prev[fullbodyStyle] || []

          if (currentList.length === 0 && fullbodyFrontUrl) {
            currentList = [{ rawUrl: fullbodyFrontRawUrl, previewUrl: fullbodyFrontUrl }]
          }

          const nextList = [...currentList, { rawUrl: rawFront, previewUrl: resolvedUrl }]

          if (nextList.length > 5) {
            nextList.shift()
          }

          targetIdx = nextList.length - 1

          return { ...prev, [fullbodyStyle]: nextList }
        })

        setFullbodyHistoryIndices(prev => ({ ...prev, [fullbodyStyle]: targetIdx }))
      }
    } catch (err) {
      setFullbodyHint(err instanceof Error ? err.message : '重新生成正面全身图失败，请重试')
    } finally {
      setFullbodyLoading(false)
    }
  }

  const confirmFullbodyFront = () => {
    if (!activeAvatarId || !fullbodyStyle) {
      return
    }

    const avatarId = activeAvatarId
    const style = fullbodyStyle
    const frontUrl = fullbodyFrontRawUrl

    // 立刻推进到 voice 阶段，不等后台的多视图生成完成
    setPhase('voice')
    setVoiceStage('describe')
    setQIndex(0)
    setInput('')
    setAnswerKind(null)
    setHint(null)

    // 异步触发确认 + 后台多视图派生
    void window.spiritagent
      .api<{
        id?: number
        asset_url?: string
      }>({
        path: `/api/companion/avatar/${avatarId}/fullbody/confirm-front`,
        method: 'POST',
        body: {
          style,
          front_url: frontUrl || undefined
        }
      })
      .then(async res => {
        await applyPortrait({
          id: res?.id,
          assetUrl: res?.asset_url
        })
        // 形象确认后立即锁死 onBack 路径(返回到 voice → q-character → portrait-avatar → fullbody-3d 会让
        // 用户重新调整正面视图,与已启动的 3D 生成不一致)。
        setImageSealed(true)
        // 形象确认后立即异步启动 3D 生成,不等 onboarding 剩余步骤(语音/性格) 完成;
        // 生成在 web 进程内 fire-and-forget,失败静默——用户在客户端随时可重试
        void window.spiritagent
          .api<{ id?: number; status?: string }>({ path: '/api/companion/model', method: 'POST', body: {} })
          .catch(() => undefined)
      })
      .catch(() => {
        // 后台派生是异步的，对用户 onboarding 流程不构成阻塞
      })
  }

  const previewVoice = (id: string, context: string) =>
    void speakScripted(sampleLine(answers.name || ''), id || undefined, context)

  // 选中时总要试听：标签本身说明不了声音听起来什么样。
  const selectVoice = (next: VoiceOption, context: string) => {
    setVoice(next)
    setCompanionVoiceId(next.id)
    previewVoice(next.id, context)
  }

  const onVoiceLangTabClick = async (lang: VoiceLanguageFilter) => {
    setVoiceLangFilter(lang)
    const r = await fetchVoiceCatalogRaw(requestGateway, lang)
    const voices = r.ok ? r.catalog.voices : []
    setVoiceCatalog(voices)
    // 候选项是按上一个 tab 的语言评分的。
    setVoiceAlternatives([])
    // 把当前 voice 重置成筛选后列表的第一项，让「试听 / 下一个」循环从一个语言适配的默认值起步。
    // 持久化的 voice id 跟随显示中的 voice，这样后续 confirmVoice 拿到的是当前 tab 的，
    // 而不是上一个 tab 的。
    const next = voices[0] ?? voice
    setVoice(next)

    if (next) {
      setCompanionVoiceId(next.id)
    }
  }

  const confirmVoice = () => {
    if (voice) {
      const vName = voice.label || voice.id
      setAnswers(prev => ({ ...prev, voice: vName }))
      void submitOnboardingAnswer('voice', vName)
    }

    setPhase('q-user')
    setQIndex(0)
    setInput('')
    setAnswerKind(null)
    setHint(null)
  }

  const finish = async (currentAnswers?: OnboardingAnswers) => {
    const ans = { ...answers, ...(currentAnswers ?? {}) }

    if (voice && !ans.voice) {
      ans.voice = voice.label || voice.id
    }

    // 兜底重试；失败时退回 'q-user'，避免 phase 卡在 'finishing'。
    try {
      if (voice) {
        await submitOnboardingAnswer('voice', voice.label || voice.id)
      }

      await onboardingSubmissionsRef.current

      await savePersona(assemblePersona(ans))
    } catch (err) {
      setPhase('q-user')
      setQIndex(USER_QUESTIONS.length - 1)
      setHint(err instanceof Error ? `同步失败：${err.message}` : '同步失败，请稍后再试')
      void playOnboardingAudio('onboarding.finishing.retry')

      return
    }

    void clearDraftRefImage()
    updateRefImage(null)
    setPhase('greeting')

    const ok = await playOnboardingAudio('onboarding.greeting')

    if (!ok) {
      setHint('（声音暂时不可用）')
    }

    await sleep(ok ? 600 : 1800)
    onCompleted()
  }

  const presetValues = question?.presets ?? []
  const otherVoices = voice ? voiceCatalog.filter(v => v.id !== voice.id) : []
  // 只要还有候选项，「换一个」就在候选项里循环。
  const voiceCandidates = voice ? [voice, ...(voiceAlternatives.length ? voiceAlternatives : otherVoices)] : []

  const currentFullbodyHistory: HistoryGalleryItem[] = useMemo(() => {
    if (!fullbodyStyle) {
      return []
    }

    const list = fullbodyHistories[fullbodyStyle] || []

    return list.map(item => ({ url: item.previewUrl }))
  }, [fullbodyHistories, fullbodyStyle])

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
          {phase === 'q-character' && question && LOCKED_FIELD_KEYS.has(question.key) && (
            <p className="mb-2 rounded-md border border-amber-300/30 bg-amber-300/10 px-2 py-1 text-[10px] leading-relaxed text-amber-200/85">
              当前字段是形象确认后无法再次更改的重点内容。
            </p>
          )}
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
                          onClick={() => updateRefImage(null)}
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
                    disabled={imageSealed || (phase === 'q-character' && qIndex === 0)}
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

          {phase === 'hatching' && <SpinnerWithText size="h-6 w-6" text={hint || '让我想想我该是什么样子…'} />}

          {(phase === 'portrait-avatar' || phase === 'greeting') && (
            <PortraitPanel
              avatarUrl={portraitUrl}
              hint={portraitPanelHint}
              history={currentHistoryItems}
              introHint={phase === 'portrait-avatar' ? portraitIntroHint() : null}
              name={answers.name?.trim() || '伙伴'}
              onSelectEntry={onSelectHistoryEntry}
              selectedIdx={portraitSelectedIdx}
            />
          )}

          {phase === 'portrait-avatar' && (
            <div className="mt-4">
              {avatarBusy ? (
                <SpinnerWithText text="正在重新生成头像…" />
              ) : (
                <>
                  <RegenFeedbackInput />
                  <div className="mt-2 space-y-2 text-xs">
                    {refImage && (
                      <div className="flex items-center gap-2">
                        <img alt="形象参考图" className="h-9 w-9 rounded-md object-cover" src={refImage.previewUrl} />
                        <span className="text-[10px] text-white/35">形象参考图（每次重生都会携带）</span>
                        <button
                          className="ml-auto text-white/40 transition hover:text-white"
                          onClick={() => updateRefImage(null)}
                          type="button"
                        >
                          移除
                        </button>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <button
                        className="rounded-full border border-dashed border-white/25 px-3 py-1 text-white/70 transition hover:bg-white/10"
                        onClick={() => void pickPresentationImage()}
                        type="button"
                      >
                        {presentationRef ? '换风格参考图' : '＋ 风格参考图'}
                      </button>
                      {presentationRef && (
                        <>
                          <img
                            alt="风格参考"
                            className="h-9 w-9 rounded-md object-cover"
                            src={presentationRef.previewUrl}
                          />
                          <span className="text-[10px] text-white/35">参考风格/展现形式，不影响角色形象</span>
                          <button
                            className="ml-auto text-white/40 transition hover:text-white"
                            onClick={() => setPresentationRef(null)}
                            type="button"
                          >
                            移除
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-xs">
                    <div className="flex gap-3">
                      <button
                        className="text-white/60 transition hover:text-white disabled:opacity-40"
                        onClick={() => {
                          setPhase('q-character')
                          setQIndex(CHARACTER_QUESTIONS.length - 1)
                        }}
                        type="button"
                      >
                        上一步
                      </button>
                      <button
                        className="text-white/70 transition hover:text-white disabled:opacity-40"
                        onClick={() => void regenerateAvatarPortrait()}
                        type="button"
                      >
                        重新生成
                      </button>
                    </div>
                    <button
                      className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white"
                      disabled={activeAvatarId == null}
                      onClick={() => void confirmPortrait()}
                      type="button"
                    >
                      确认
                    </button>
                  </div>
                </>
              )}
              {portraitPanelHint && <p className="mt-2 text-xs text-rose-300/90">{portraitPanelHint}</p>}
            </div>
          )}

          {phase === 'fullbody-3d' && (
            <div className="mt-2">
              {fullbodyLoading ? (
                <div className="py-8 text-center">
                  <SpinnerWithText size="h-6 w-6" text={fullbodyLoadingText} />
                </div>
              ) : !fullbodyStyle ? (
                <div>
                  <p className="text-[14px] font-medium text-white/90">选择全身立绘画风</p>
                  <p className="mt-1 text-xs text-white/60">
                    为您生成了两种不同风格的正面全身样图，点击卡片选择您喜欢的画风，也可放大预览：
                  </p>
                  <div
                    className={`mt-3 grid gap-3 ${
                      styleCatalog.length <= 2 ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3'
                    }`}
                  >
                    {styleCatalog.map(style => {
                      const isSelected = selectedStyleKey === style.id
                      const sampleUrl = fullbodySamples[style.id]

                      return (
                        <div
                          className={`group relative flex flex-col items-center rounded-xl border p-2.5 text-left transition cursor-pointer ${
                            isSelected
                              ? 'border-white/80 bg-white/15 shadow-lg ring-1 ring-white/40'
                              : 'border-white/15 bg-white/5 hover:border-white/40 hover:bg-white/10'
                          }`}
                          key={style.id}
                          onClick={() => setSelectedStyleKey(style.id)}
                        >
                          <div className="relative aspect-[9/16] w-full overflow-hidden rounded-lg bg-black/30">
                            {sampleUrl ? (
                              <>
                                <img alt={style.label_zh} className="h-full w-full object-cover" src={sampleUrl} />
                                <button
                                  aria-label="放大预览"
                                  className="absolute top-1.5 right-1.5 rounded-full bg-black/60 p-1.5 text-white/80 backdrop-blur-sm transition hover:bg-black/80 hover:text-white"
                                  onClick={e => {
                                    e.stopPropagation()
                                    setFullbodyZoomUrl(sampleUrl)
                                  }}
                                  title="放大预览"
                                  type="button"
                                >
                                  <svg
                                    className="h-3.5 w-3.5"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth={2}
                                    viewBox="0 0 24 24"
                                  >
                                    <path
                                      d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v6m3-3H7"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    />
                                  </svg>
                                </button>
                              </>
                            ) : (
                              <div className="flex h-full w-full items-center justify-center text-xs text-white/40">
                                加载中…
                              </div>
                            )}
                            {isSelected && (
                              <div className="absolute top-1.5 left-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-white text-black shadow">
                                <svg
                                  className="h-3 w-3"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth={3}
                                  viewBox="0 0 24 24"
                                >
                                  <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
                                </svg>
                              </div>
                            )}
                          </div>
                          <div className="mt-2 w-full text-center">
                            <span
                              className={`rounded-full px-2 py-0.5 text-[11px] font-medium transition ${
                                isSelected ? 'bg-white text-black' : 'bg-white/15 text-white/90'
                              }`}
                            >
                              {style.label_zh}
                            </span>
                            {style.description_zh && (
                              <p className="mt-1 text-[10px] text-white/50">{style.description_zh}</p>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  {fullbodyHint && <p className="mt-2 text-xs text-rose-300/90">{fullbodyHint}</p>}
                  <div className="mt-4 flex items-center justify-between text-xs">
                    <button
                      className="text-white/60 transition hover:text-white"
                      onClick={() => setPhase('portrait-avatar')}
                      type="button"
                    >
                      上一步
                    </button>
                    <button
                      className="rounded-full bg-white/90 px-4 py-1.5 font-medium text-black transition hover:bg-white disabled:opacity-40"
                      disabled={!selectedStyleKey || !fullbodySamples[selectedStyleKey]}
                      onClick={() => selectStyle(selectedStyleKey)}
                      type="button"
                    >
                      确认画风
                    </button>
                  </div>
                </div>
              ) : (
                <div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-white/60">当前画风：</span>
                      <span className="rounded-full bg-white/20 px-2.5 py-0.5 text-xs font-medium text-white">
                        {styleCatalog.find(s => s.id === fullbodyStyle)?.label_zh || fullbodyStyle || ''}
                      </span>
                    </div>
                    <button
                      className="text-xs text-white/60 transition hover:text-white"
                      onClick={() => setFullbodyStyleState(null)}
                      type="button"
                    >
                      更换画风
                    </button>
                  </div>

                  <div className="relative mx-auto mt-3 flex aspect-[9/16] max-h-[320px] w-auto items-center justify-center overflow-hidden rounded-xl border border-white/15 bg-black/40 group">
                    {fullbodyFrontUrl ? (
                      <button
                        aria-label="放大查看"
                        className="relative block h-full w-full cursor-zoom-in overflow-hidden border-0 bg-transparent p-0"
                        onClick={() => setFullbodyZoomUrl(fullbodyFrontUrl)}
                        type="button"
                      >
                        <img alt="正面全身立绘" className="h-full w-full object-cover" src={fullbodyFrontUrl} />
                        <div className="absolute top-2 right-2 rounded-full bg-black/60 p-1.5 text-white/80 opacity-0 backdrop-blur-sm transition group-hover:opacity-100 hover:bg-black/80 hover:text-white">
                          <svg
                            className="h-3.5 w-3.5"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth={2}
                            viewBox="0 0 24 24"
                          >
                            <path
                              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v6m3-3H7"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </div>
                      </button>
                    ) : (
                      <div className="text-xs text-white/40">暂无预览图</div>
                    )}
                  </div>

                  {currentFullbodyHistory.length > 1 && (
                    <div className="mt-2">
                      <HistoryGallery
                        entries={currentFullbodyHistory}
                        onSelect={onSelectFullbodyHistoryEntry}
                        selectedIdx={fullbodyHistoryIndices[fullbodyStyle] ?? currentFullbodyHistory.length - 1}
                      />
                    </div>
                  )}

                  <div className="mt-3">
                    <textarea
                      className={`${INPUT_CLASS} text-xs`}
                      maxLength={MAX_APPEARANCE}
                      onChange={e => setFullbodyFeedback(e.target.value)}
                      placeholder="对正面立绘有微调要求？例如：头发再长一点、换个服饰配色…（可留空直接确认）"
                      rows={2}
                      value={fullbodyFeedback}
                    />
                  </div>

                  {fullbodyHint && <p className="mt-2 text-xs text-rose-300/90">{fullbodyHint}</p>}

                  <div className="mt-3 flex items-center justify-between text-xs">
                    <button
                      className="text-white/70 transition hover:text-white disabled:opacity-40"
                      disabled={fullbodyLoading}
                      onClick={() => void regenerateFullbodyFront()}
                      type="button"
                    >
                      微调重绘
                    </button>
                    <button
                      className="rounded-full bg-white/90 px-4 py-1.5 font-medium text-black transition hover:bg-white disabled:opacity-40"
                      disabled={fullbodyLoading || !fullbodyFrontUrl}
                      onClick={() => void confirmFullbodyFront()}
                      type="button"
                    >
                      确认形象
                    </button>
                  </div>
                </div>
              )}

              {fullbodyZoomUrl && (
                <PortraitLightbox
                  name={answers.name?.trim() || '伙伴'}
                  onClose={() => setFullbodyZoomUrl(null)}
                  url={fullbodyZoomUrl}
                />
              )}
            </div>
          )}

          {phase === 'voice' && voiceStage === 'catalog' && voice && (
            <div className="mt-1">
              <p className="mb-3 text-[13px] text-white/70">挑一个我说话的声音吧，随时可以试听。</p>
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

              <div className="rounded-xl border border-white/25 bg-white/10 p-3">
                <p className="mb-1 text-[10px] tracking-wide text-white/45">为你推荐</p>
                <div className="flex items-start justify-between gap-3 text-xs text-white/85">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{voice.label}</p>
                    {voice.tags.length > 0 && (
                      <p className="mt-0.5 truncate text-[10px] text-white/40">{voice.tags.join(' · ')}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-3">
                    <button
                      className="transition hover:text-white disabled:opacity-40"
                      disabled={voicePreparing}
                      onClick={() => previewVoice(voice.id, 'onboarding.voice.preview.try')}
                      type="button"
                    >
                      试听
                    </button>
                    <button
                      className="transition hover:text-white disabled:opacity-40"
                      disabled={voicePreparing}
                      onClick={() => selectVoice(nextVoice(voice.id, voiceCandidates), 'onboarding.voice.preview.next')}
                      type="button"
                    >
                      换一个
                    </button>
                  </div>
                </div>
              </div>

              <p className="mt-3 mb-1 text-[10px] tracking-wide text-white/45">浏览目录</p>
              <div className="max-h-48 overflow-y-auto rounded-xl border border-white/10 bg-white/5">
                {otherVoices.length === 0 ? (
                  <p className="px-3 py-4 text-center text-[10px] text-white/35">没有更多音色可选</p>
                ) : (
                  otherVoices.map(v => (
                    <button
                      className="flex w-full items-center justify-between gap-3 border-b border-white/5 px-3 py-2 text-left text-xs text-white/75 transition last:border-b-0 hover:bg-white/10 disabled:opacity-40"
                      disabled={voicePreparing}
                      key={v.id}
                      onClick={() => selectVoice(v, 'onboarding.voice.preview.try')}
                      type="button"
                    >
                      <span className="min-w-0">
                        <span className="block truncate">{v.label}</span>
                        {v.tags.length > 0 && (
                          <span className="block truncate text-[10px] text-white/35">{v.tags.join(' · ')}</span>
                        )}
                      </span>
                      <span className="shrink-0 text-[10px] text-white/35">试听并选择</span>
                    </button>
                  ))
                )}
              </div>
              <p className="mt-1 text-[10px] text-white/40">
                {voiceCatalog.length} 个音色 · 先挑个差不多的就行，以后随时能在设置里调。
              </p>
              <div className="mt-3 flex items-center justify-between gap-3 text-xs">
                <button
                  className="text-white/60 transition hover:text-white"
                  onClick={() => setVoiceStage('describe')}
                  type="button"
                >
                  上一步
                </button>
                <button
                  className="flex-1 rounded-full bg-white/90 py-1.5 text-sm font-medium text-black transition hover:bg-white"
                  onClick={confirmVoice}
                  type="button"
                >
                  使用这个
                </button>
              </div>
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
