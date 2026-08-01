import { useStore } from '@nanostores/react'
import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { registerInteractiveRegion, unregisterInteractiveRegion } from '@/companion/interactive-regions'
import { $gatewayState } from '@/shared/store/gateway'

import { clearClipCatalog } from '../clip-store'
import { assemblePersona, type OnboardingAnswers } from '../persona'
import { setCompanionVoiceId } from '../prefs'
import { Silhouette } from '../sprite/silhouette'
import { speak, stopSpeaking } from '../tts'
import { fetchVoiceCatalog, matchVoicePreference, nextVoice, sampleLine, type VoiceOption } from '../voice'

type Phase = 'q' | 'hatching' | 'portrait' | 'voice' | 'finishing' | 'greeting'
type QKey = keyof OnboardingAnswers

interface Question {
  key: QKey
  text: string
  placeholder: string
  required: boolean
  multiline: boolean
  presets: readonly string[]
}

const QUESTIONS: readonly Question[] = [
  { key: 'name', text: '您好…我还不认识自己。您愿意给我一个名字吗？', placeholder: '给我起个名字吧', required: true, multiline: false, presets: [] },
  {
    key: 'role',
    text: '好的，我会是 {name}。那您希望我是什么样的存在？爱人、秘书、还是专属的“贾维斯”？',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    presets: ['爱人', '秘书', '专属管家', '无话不谈的朋友']
  },
  {
    key: 'personality',
    text: '您希望我是什么性格？活泼好动、温柔体贴、冷静理性…还是别的？',
    placeholder: '自由描述…',
    required: false,
    multiline: false,
    presets: ['温柔体贴', '活泼好动', '冷静理性', '毒舌傲娇']
  },
  {
    key: 'selfIntro',
    text: '说说您自己吧——您是谁，平时在忙什么，有什么在意的事？',
    placeholder: '（说说你自己，让我更懂你）',
    required: false,
    multiline: true,
    presets: []
  },
  {
    key: 'voice',
    text: '您希望我听起来是什么样的？比如温柔的少女音、沉稳的男声、活泼的正太…',
    placeholder: '描述你想要的声音…',
    required: false,
    multiline: false,
    presets: ['温柔少女音', '沉稳男声', '活泼正太', '清冷御姐']
  }
]

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

// Halo padding for the silhouette's CSS glow. With `flex items-center` the
// silhouette sits centered inside the container's 448px-wide row, so its
// glow (170% × 170% = ~56px overflow on each side) stays well inside the
// container's bounding box — no extra padding needed.
const DRAG_THRESHOLD = 6

// Desktop answer keys → Backend ONBOARDING_FIELDS (services/companion/persona_service.py).
const BACKEND_FIELD: Record<QKey, string> = { name: 'name', role: 'role', personality: 'personality', selfIntro: 'self_intro', voice: 'voice' }

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
  } catch {
    return false
  }
}

interface OnboardingFlowProps {
  onCompleted: () => void
}

export function OnboardingFlow({ onCompleted }: OnboardingFlowProps) {
  const gatewayState = useStore($gatewayState)
  const { requestGateway } = useGatewayRequest()
  const [phase, setPhase] = useState<Phase>('q')
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const resumedRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean; pointerId: number } | null>(null)

  // Centered initial position; the user can drag from there.
  const [dialogPos, setDialogPos] = useState<{ x: number; y: number }>(() => {
    const width = 448
    const height = 600

    if (typeof window === 'undefined') {return { x: 0, y: 0 }}

    return {
      x: Math.max(0, Math.round((window.innerWidth - width) / 2)),
      y: Math.max(0, Math.round((window.innerHeight - height) / 2))
    }
  })

  // Onboarding dialog is fully interactive — register its actual visible rect
  // with the global interactive-regions registry so SpriteStage's hit-test
  // captures only while the cursor is over the dialog silhouette + form.
  // SpriteStage restores click-through on unmount.
  useEffect(() => {
    registerInteractiveRegion('onboarding', () => {
      const rect = containerRef.current?.getBoundingClientRect() ?? null

      if (!rect || rect.width === 0 || rect.height === 0) {return null}

      return rect
    })

    return () => {
      stopSpeaking()
      unregisterInteractiveRegion('onboarding')
    }
  }, [])

  // Drag uses document-level listeners (not React onPointerMove on the
  // container) so the drag survives the cursor leaving the dialog rect and
  // still updates while the cursor is over an unrelated region. setPointerCapture
  // would interfere with click events fired on the form's buttons/inputs.
  const onDialogPointerDown = (e: ReactPointerEvent) => {
    const target = e.target as HTMLElement

    if (target.closest('button, input, textarea, [contenteditable="true"]')) {return}

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

      if (!drag || drag.pointerId !== e.pointerId) {return}

      const dx = e.clientX - drag.startX
      const dy = e.clientY - drag.startY

      if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) {return}
      drag.moved = true
      setDialogPos({ x: drag.originX + dx, y: drag.originY + dy })
    }

    const onUp = (e: PointerEvent) => {
      const drag = dragRef.current

      if (!drag || drag.pointerId !== e.pointerId) {return}
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
    if (resumedRef.current || gatewayState !== 'open') {return}
    resumedRef.current = true

    void (async () => {
      try {
        const state = await requestGateway<{ answers?: Record<string, string>; next_field?: string | null; complete?: boolean }>('onboarding.get_state', {})

        if (state?.complete) {
          onCompleted()

          return
        }

        if (state?.answers) {
          setAnswers({
            name: state.answers.name,
            role: state.answers.role,
            personality: state.answers.personality,
            selfIntro: state.answers.self_intro,
            voice: state.answers.voice
          })
          const idx = QUESTIONS.findIndex(q => BACKEND_FIELD[q.key] === state.next_field)

          if (idx > 0) {setQIndex(idx)}
        }
      } catch {
        /* no draft yet — start fresh */
      }

      setVoiceCatalog((await fetchVoiceCatalog(requestGateway)).voices)
    })()
  }, [gatewayState, requestGateway, onCompleted])

  const question = QUESTIONS[qIndex]
  const spokenText = question.text.replace('{name}', answers.name?.trim() || '你')

  // Speak each question as it appears (default neutral voice; plan §3.2).
  useEffect(() => {
    if (phase !== 'q') {return}
    setInput((answers[question.key] as string) ?? '')
    setHint(null)
    void speak(spokenText)

    return () => stopSpeaking()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, qIndex])

  useEffect(() => {
    if (phase === 'q') {(question.multiline ? textareaRef.current : inputRef.current)?.focus()}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, qIndex])

  const commit = (value: string | undefined) => {
    const q = QUESTIONS[qIndex]
    const cleaned = value && value.trim() ? value.trim() : undefined
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
    if (question.required) {return}
    commit(undefined)
    advance()
  }

  const onBack = () => {
    if (qIndex === 0) {return}
    setQIndex(qIndex - 1)
  }

  const enterHatching = async () => {
    setPhase('hatching')
    setHint(null)
    void speak('让我想想我该是什么样子…')

    // Finalize the persona BEFORE generating the portrait — the backend's
    // avatar generation requires a complete persona (is_complete=true). The
    // answers are all collected by this point; self_intro/voice were already
    // consumed by the draft + voice matching below. Retries, then proceeds
    // regardless so the user is never stranded.
    let personaOk = false

    for (let i = 0; i < 3; i++) {
      if (await savePersona(assemblePersona(answers))) {personaOk = true;

 break}

      await sleep(700)
    }

    let url: string | null = null

    if (personaOk) {
      for (let i = 0; i < 3; i++) {
        url = await generatePortrait()

        if (url) {break}
        setHint('我还没想好…')
        await sleep(900)
      }
    } else {
      setHint('记忆还没存好，稍后再试试形象吧…')
    }

    setPortraitUrl(url)
    setPhase('portrait')
    void speak(url ? '嗯…这就是我，您觉得怎么样？' : '我大概长这样，您觉得呢？')
  }

  const regeneratePortrait = async () => {
    setBusy(true)
    setHint(null)
    clearClipCatalog()

    // avatar.regenerate invalidates derivative clips on the backend (design
    // §5.1.A) and re-seeds batch 0 after the new portrait succeeds.
    try {
      const res = await requestGateway<{ asset_url?: string }>('avatar.regenerate', { feedback: undefined })

      if (res?.asset_url) {
        setPortraitUrl(res.asset_url)
        void speak('换一个样子，这样如何？')
      } else {
        setHint('暂时换不出来，稍后再试吧')
      }
    } catch {
      setHint('暂时换不出来，稍后再试吧')
    } finally {
      setBusy(false)
    }
  }

  const uploadPortrait = async () => {
    try {
      const [path] = await window.deskagent.selectPaths({ title: '选择一张图片作为形象', filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }] })

      if (!path) {return}
      const dataUrl = await window.deskagent.readFileDataUrl(path)
      const comma = dataUrl.indexOf(',')
      const mime = comma > 0 ? dataUrl.slice(5, comma) : 'image/png'
      const base64 = comma > 0 ? dataUrl.slice(comma + 1) : ''

      if (!base64) {return}

      // POST base64 JSON — the desktop REST IPC speaks JSON, not multipart.
      const res = await window.deskagent.api<{ asset_url?: string }>({
        path: '/api/companion/avatar/upload',
        method: 'POST',
        body: { image: base64, content_type: mime }
      })

      if (res?.asset_url) {
        clearClipCatalog()
        setPortraitUrl(res.asset_url)
        void speak('用你给的样子，这样如何？')
      }
    } catch {
      setHint('上传失败了，换张图试试？')
    }
  }

  const confirmPortrait = async () => {
    const { voice: matched } = await matchVoicePreference(requestGateway, answers.voice ?? '')
    setVoice(matched)
    setCompanionVoiceId(matched.id)
    setPhase('voice')
    void speak(sampleLine(answers.name || ''), matched.id || undefined)
  }

  const confirmVoice = () => {
    setPhase('finishing')
    void finish()
  }

  const finish = async () => {
    // Persona was finalized at hatching; this is a no-op safety net if that
    // save failed — retry once more so chat has a personality injected.
    await savePersona(assemblePersona(answers))

    setPhase('greeting')
    const ok = await speak(`您好，我是${answers.name?.trim() || '您的伙伴'}。很高兴见到您！`)

    if (!ok) {setHint('（声音暂时不可用）')}
    await sleep(ok ? 600 : 1800)
    onCompleted()
  }

  const clarity = phase === 'q' ? (qIndex + (input ? 0.5 : 0)) / QUESTIONS.length : 1
  const presetValues = question?.presets ?? []

  return (
    <div
      className="fixed inset-0 z-50 pointer-events-none"
      style={{ pointerEvents: 'none' }}
    >
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

        <div className="w-full rounded-2xl border border-white/10 bg-black/45 p-5 text-white shadow-2xl backdrop-blur-md" style={{ pointerEvents: 'auto' }}>
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
                  if (e.key === 'Enter' && !question.multiline) {onSend()}
                }}
                placeholder={question.placeholder}
                ref={inputRef}
                value={input}
              />
            )}
            <div className="mt-4 flex items-center justify-between text-xs">
              <button className="text-white/60 transition hover:text-white disabled:opacity-30" disabled={qIndex === 0} onClick={onBack} type="button">
                上一题
              </button>
              <div className="flex gap-3">
                {!question.required && (
                  <button className="text-white/60 transition hover:text-white" onClick={onSkip} type="button">
                    跳过
                  </button>
                )}
                <button className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white" onClick={onSend} type="button">
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

        {(phase === 'portrait' || phase === 'voice' || phase === 'greeting') && (
          <PortraitPanel name={answers.name?.trim() || '伙伴'} url={portraitUrl} />
        )}

        {phase === 'portrait' && (
          <div className="mt-4 flex items-center justify-between text-xs">
            <div className="flex gap-3">
              <button className="text-white/70 transition hover:text-white disabled:opacity-40" disabled={busy} onClick={regeneratePortrait} type="button">
                {busy ? '生成中…' : '重新生成'}
              </button>
              <button className="text-white/70 transition hover:text-white disabled:opacity-40" disabled={busy} onClick={uploadPortrait} type="button">
                自己上传
              </button>
            </div>
            <button className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white" onClick={confirmPortrait} type="button">
              就这样吧
            </button>
          </div>
        )}

        {phase === 'voice' && voice && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-white/70">
              <span>{voice.label}</span>
              <div className="flex gap-3">
                <button className="transition hover:text-white" onClick={() => void speak(sampleLine(answers.name || ''), voice?.id || undefined)} type="button">
                  试听
                </button>
                <button
                  className="transition hover:text-white"
                  onClick={() => {
                    const n = nextVoice(voice.id, voiceCatalog.length ? voiceCatalog : [voice])
                    setVoice(n)
                    setCompanionVoiceId(n.id)
                    void speak(sampleLine(answers.name || ''), n.id || undefined)
                  }}
                  type="button"
                >
                  换一个
                </button>
              </div>
            </div>
            <p className="mt-1 text-[10px] text-white/40">先挑个差不多的就行，以后随时能在设置里调。</p>
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
            <p className="text-center text-sm text-white/90">您好，我是{answers.name?.trim() || '您的伙伴'}。很高兴见到您！</p>
            {hint && <p className="mt-1 text-center text-[10px] text-white/40">{hint}</p>}
          </div>
        )}
        </div>
      </div>
    </div>
  )
}

function PortraitPanel({ url, name }: { url: string | null; name: string }) {
  return (
    <div className="flex justify-center">
      {url ? (
        <img alt={name} className="h-40 w-40 rounded-xl object-cover shadow-lg" src={url} />
      ) : (
        <div className="grid h-40 w-40 place-items-center rounded-xl bg-white/5 text-center text-xs text-white/50">
          {name}
        </div>
      )}
    </div>
  )
}
