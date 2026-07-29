import { useEffect, useRef, useState } from 'react'

import { matchVoice, nextVoice, sampleLine, type VoiceOption } from './backend-companion-mock'
import { Silhouette } from './silhouette'
import { speak, stopSpeaking } from './tts'
import { assemblePersona, type OnboardingAnswers } from './persona'

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
  const [phase, setPhase] = useState<Phase>('q')
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // The onboarding surface is fully interactive — disable click-through while
  // mounted so the text inputs work without per-element hit-testing. Restore
  // click-through (with mousemove forwarding) on unmount.
  useEffect(() => {
    void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: false })
    return () => {
      stopSpeaking()
      void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
    }
  }, [])

  const question = QUESTIONS[qIndex]
  const spokenText = question.text.replace('{name}', answers.name?.trim() || '你')

  // Speak each question as it appears (default neutral voice; plan §3.2).
  useEffect(() => {
    if (phase !== 'q') return
    setInput((answers[question.key] as string) ?? '')
    setHint(null)
    void speak(spokenText)
    return () => stopSpeaking()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, qIndex])

  useEffect(() => {
    if (phase === 'q') (question.multiline ? textareaRef.current : inputRef.current)?.focus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, qIndex])

  const commit = (value: string | undefined) => {
    const q = QUESTIONS[qIndex]
    setAnswers(prev => ({ ...prev, [q.key]: value && value.trim() ? value.trim() : undefined }))
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
    if (question.required) return
    commit(undefined)
    advance()
  }

  const onBack = () => {
    if (qIndex === 0) return
    setQIndex(qIndex - 1)
  }

  const enterHatching = async () => {
    setPhase('hatching')
    setHint(null)
    void speak('让我想想我该是什么样子…')
    let url: string | null = null
    for (let i = 0; i < 3; i++) {
      url = await generatePortrait()
      if (url) break
      setHint('我还没想好…')
      await sleep(900)
    }
    setPortraitUrl(url)
    setPhase('portrait')
    void speak(url ? '嗯…这就是我，您觉得怎么样？' : '我大概长这样，您觉得呢？')
  }

  const regeneratePortrait = async () => {
    setBusy(true)
    setHint(null)
    const url = await generatePortrait()
    setBusy(false)
    if (url) {
      setPortraitUrl(url)
      void speak('换一个样子，这样如何？')
    } else {
      setHint('暂时换不出来，稍后再试吧')
    }
  }

  const confirmPortrait = () => {
    const matched = matchVoice(answers.voice)
    setVoice(matched)
    setPhase('voice')
    void speak(sampleLine(answers.name || ''))
  }

  const confirmVoice = () => {
    setPhase('finishing')
    void finish()
  }

  const finish = async () => {
    // Persist persona so Slice 3 chat has a personality injected. Required
    // fields are defaulted in assemblePersona so the PUT succeeds even with
    // skips (plan §4). Retries, then proceeds regardless — never strand the
    // user on a save failure.
    for (let i = 0; i < 3; i++) {
      if (await savePersona(assemblePersona(answers))) break
      await sleep(700)
    }
    setPhase('greeting')
    const ok = await speak(`您好，我是${answers.name?.trim() || '您的伙伴'}。很高兴见到您！`)
    if (!ok) setHint('（声音暂时不可用）')
    await sleep(ok ? 600 : 1800)
    onCompleted()
  }

  const clarity = phase === 'q' ? (qIndex + (input ? 0.5 : 0)) / QUESTIONS.length : 1
  const presetValues = question?.presets ?? []

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 px-6" style={{ pointerEvents: 'auto' }}>
      <Silhouette clarity={clarity} spin={phase === 'hatching'} size={160} />

      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-black/45 p-5 text-white shadow-2xl backdrop-blur-md">
        {phase === 'q' && (
          <>
            <p className="min-h-[3.5rem] text-[15px] leading-relaxed">{spokenText}</p>
            {presetValues.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {presetValues.map(p => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setInput(p)}
                    className="rounded-full border border-white/20 bg-white/5 px-3 py-1 text-xs transition hover:bg-white/15"
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}
            {question.multiline ? (
              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                placeholder={question.placeholder}
                rows={3}
                className="mt-3 w-full resize-none rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm outline-none placeholder:text-white/40 focus:border-white/40"
              />
            ) : (
              <input
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                placeholder={question.placeholder}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !question.multiline) onSend()
                }}
                className="mt-3 w-full rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm outline-none placeholder:text-white/40 focus:border-white/40"
              />
            )}
            <div className="mt-4 flex items-center justify-between text-xs">
              <button type="button" onClick={onBack} disabled={qIndex === 0} className="text-white/60 transition hover:text-white disabled:opacity-30">
                上一题
              </button>
              <div className="flex gap-3">
                {!question.required && (
                  <button type="button" onClick={onSkip} className="text-white/60 transition hover:text-white">
                    跳过
                  </button>
                )}
                <button type="button" onClick={onSend} className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white">
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
          <PortraitPanel url={portraitUrl} name={answers.name?.trim() || '伙伴'} />
        )}

        {phase === 'portrait' && (
          <div className="mt-4 flex items-center justify-between text-xs">
            <button type="button" onClick={regeneratePortrait} disabled={busy} className="text-white/70 transition hover:text-white disabled:opacity-40">
              {busy ? '生成中…' : '重新生成'}
            </button>
            <button type="button" onClick={confirmPortrait} className="rounded-full bg-white/90 px-4 py-1 font-medium text-black transition hover:bg-white">
              就这样吧
            </button>
          </div>
        )}

        {phase === 'voice' && voice && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-white/70">
              <span>{voice.label}</span>
              <div className="flex gap-3">
                <button type="button" onClick={() => void speak(sampleLine(answers.name || ''))} className="transition hover:text-white">
                  试听
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const n = nextVoice(voice.id)
                    setVoice(n)
                    void speak(sampleLine(answers.name || ''))
                  }}
                  className="transition hover:text-white"
                >
                  换一个
                </button>
              </div>
            </div>
            <p className="mt-1 text-[10px] text-white/40">先挑个差不多的就行，以后随时能在设置里调。</p>
            <button
              type="button"
              onClick={confirmVoice}
              className="mt-3 w-full rounded-full bg-white/90 py-1.5 text-sm font-medium text-black transition hover:bg-white"
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
  )
}

function PortraitPanel({ url, name }: { url: string | null; name: string }) {
  return (
    <div className="flex justify-center">
      {url ? (
        <img src={url} alt={name} className="h-40 w-40 rounded-xl object-cover shadow-lg" />
      ) : (
        <div className="grid h-40 w-40 place-items-center rounded-xl bg-white/5 text-center text-xs text-white/50">
          {name}
        </div>
      )}
    </div>
  )
}
