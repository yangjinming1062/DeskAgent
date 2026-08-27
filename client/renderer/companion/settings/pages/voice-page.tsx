import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $persona } from '@/companion/persona-store'
import { $companionVoiceId, setCompanionVoiceId } from '@/companion/prefs'
import { speakScripted } from '@/companion/tts'
import {
  designVoice,
  fetchVoiceCatalogRaw,
  GENDER_OPTIONS,
  LANGUAGE_LABELS,
  playDataUrl,
  sampleLine,
  type VoiceCatalog,
  type VoiceDesignPreview
} from '@/companion/voice'
import { Check } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CHIP_FILTER,
  CHIP_FILTER_ACTIVE,
  HINT_TEXT,
  INPUT_CLASS,
  SECTION_TITLE,
  SettingsPage
} from '@/shared/panel'

// 音色页：目录筛选 / 试听 / 切换 + 专属音色设计。
export function VoicePage(): React.ReactElement {
  const { requestGateway } = useGatewayRequest()
  const persona = useStore($persona)
  const currentVoice = useStore($companionVoiceId)

  const [catalog, setCatalog] = useState<VoiceCatalog>({
    provider: '',
    voices: [],
    supportsVoiceDesign: false,
    voiceDesignGuide: ''
  })

  const [langFilter, setLangFilter] = useState('')
  const [genderFilter, setGenderFilter] = useState('')

  const [designPrompt, setDesignPrompt] = useState('')
  const [designPreview, setDesignPreview] = useState<VoiceDesignPreview | null>(null)
  const [designing, setDesigning] = useState(false)
  const [designHint, setDesignHint] = useState<string | null>(null)

  const filteredVoices = useMemo(
    () =>
      catalog.voices.filter(
        v =>
          (!langFilter || v.language === langFilter || v.language === 'multi') &&
          (!genderFilter || v.gender === genderFilter)
      ),
    [catalog.voices, langFilter, genderFilter]
  )

  const langOptions = useMemo(() => {
    const langs = new Set(catalog.voices.map(v => v.language).filter(Boolean))

    return ['', ...Array.from(langs).sort()]
  }, [catalog.voices])

  useEffect(() => {
    void fetchVoiceCatalogRaw(requestGateway).then(r => {
      if (r.ok) {
        setCatalog(r.catalog)
      }
    })
  }, [requestGateway])

  const runDesign = async (): Promise<void> => {
    const prompt = designPrompt.trim()

    if (!prompt) {
      return
    }

    setDesigning(true)
    setDesignHint(null)

    try {
      const result = await designVoice(requestGateway, prompt, sampleLine(persona?.name ?? ''))
      setDesignPreview(result)
      playDataUrl(result.trialAudioDataUrl)
    } catch {
      setDesignHint('生成失败，换个描述试试？')
    } finally {
      setDesigning(false)
    }
  }

  if (catalog.voices.length === 0) {
    return (
      <SettingsPage title="音色">
        <p className="text-xs text-white/40">未配置 TTS 引擎，使用默认音色。</p>
      </SettingsPage>
    )
  }

  return (
    <SettingsPage hint="选择伙伴的说话音色，或设计一个专属音色。" title="音色">
      <div className="flex flex-wrap items-center gap-1.5">
        {langOptions.map(lang => (
          <button
            className={langFilter === lang ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
            key={lang}
            onClick={() => setLangFilter(lang)}
            type="button"
          >
            {lang ? (LANGUAGE_LABELS[lang] ?? lang) : '全部'}
          </button>
        ))}
        <span className="mx-1 text-white/20">|</span>
        {GENDER_OPTIONS.map(g => (
          <button
            className={genderFilter === g.id ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
            key={g.id}
            onClick={() => setGenderFilter(g.id)}
            type="button"
          >
            {g.label}
          </button>
        ))}
      </div>

      <div className="mt-3 divide-y divide-white/5 overflow-hidden rounded-xl border border-white/8 bg-surface-card">
        {filteredVoices.map(v => {
          const inUse = currentVoice === v.id

          return (
            <div className="flex items-center justify-between gap-3 px-3.5 py-2.5" key={v.id}>
              <div className="min-w-0">
                <p className={cn('text-xs', inUse ? 'font-medium text-white' : 'text-white/90')}>
                  {v.label}
                  {inUse && <Check className="ml-1.5 inline size-3.5 text-emerald-400" />}
                </p>
                <p className="mt-0.5 text-[11px] text-white/40">{v.tags.join(' · ')}</p>
              </div>
              <div className="flex shrink-0 gap-1.5">
                <button
                  className="rounded-lg px-2.5 py-1 text-xs text-white/60 transition hover:bg-white/10 hover:text-white"
                  onClick={() =>
                    void speakScripted(sampleLine(persona?.name ?? ''), v.id || undefined, 'voice.preview')
                  }
                  type="button"
                >
                  试听
                </button>
                <button
                  className={cn(BTN_SUBTLE, 'h-7 px-3', inUse && 'border-emerald-400/30 text-emerald-300')}
                  disabled={inUse}
                  onClick={() => setCompanionVoiceId(v.id)}
                  type="button"
                >
                  {inUse ? '使用中' : '使用'}
                </button>
              </div>
            </div>
          )
        })}
        {filteredVoices.length === 0 && (
          <p className="px-3.5 py-6 text-center text-xs text-white/35">当前筛选无匹配音色。</p>
        )}
      </div>

      {catalog.supportsVoiceDesign && (
        <section className="mt-5">
          <p className={cn(SECTION_TITLE, 'mb-2')}>设计专属音色</p>
          <div className="rounded-xl border border-white/8 bg-surface-card p-3.5">
            {catalog.voiceDesignGuide && (
              <p className={cn(HINT_TEXT, 'whitespace-pre-line')}>{catalog.voiceDesignGuide}</p>
            )}
            <textarea
              className={cn(INPUT_CLASS, 'mt-2 resize-none')}
              onChange={e => setDesignPrompt(e.target.value)}
              placeholder="描述你想要的音色…"
              rows={3}
              value={designPrompt}
            />
            <div className="mt-2.5 flex items-center gap-2">
              <button
                className={BTN_PRIMARY}
                disabled={designing || !designPrompt.trim()}
                onClick={() => void runDesign()}
                type="button"
              >
                {designing ? '生成中…' : '生成预览'}
              </button>
              {designPreview && (
                <>
                  <button
                    className="rounded-lg px-2.5 py-1 text-xs text-white/60 transition hover:bg-white/10 hover:text-white"
                    onClick={() => playDataUrl(designPreview.trialAudioDataUrl)}
                    type="button"
                  >
                    试听
                  </button>
                  <button
                    className={cn(
                      BTN_SUBTLE,
                      'h-7 px-3',
                      currentVoice === designPreview.voiceId && 'border-emerald-400/30 text-emerald-300'
                    )}
                    disabled={currentVoice === designPreview.voiceId}
                    onClick={() => setCompanionVoiceId(designPreview.voiceId)}
                    type="button"
                  >
                    {currentVoice === designPreview.voiceId ? '使用中' : '使用'}
                  </button>
                </>
              )}
            </div>
            {designHint && <p className="mt-2 text-xs text-amber-300/90">{designHint}</p>}
          </div>
        </section>
      )}
    </SettingsPage>
  )
}
