import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/shared/components/ui'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import { GENDER_OPTIONS, LANGUAGE_LABELS, type VoiceOption } from '@/shared/voice-catalog'

import { EmptyState, FilterPill, ListRow, LoadingState, Pill, SettingsContent, SettingsSubsection } from './primitives'

// 工具窗口（hub）的只配音色浏览器——无 WS 网关，通过 REST 获取同一后端配色目录，
// 经 spiritagent:media:tts IPC 试听。更换伙伴音色仍在精灵窗口的伙伴设置中完成。

interface VoiceCatalog {
  provider: string
  voices: VoiceOption[]
  supports_voice_design?: boolean
}

const PREVIEW_LINE = '你好呀，这是我的声音～'

export function VoiceGallerySettings(): React.JSX.Element {
  const t = strings.voiceGallery
  const [langFilter, setLangFilter] = useState('')
  const [genderFilter, setGenderFilter] = useState('')
  const [previewingId, setPreviewingId] = useState<string | null>(null)

  const loader = useAsyncLoader<VoiceCatalog>(async () => {
    return window.spiritagent.api<VoiceCatalog>({ path: '/api/companion/voices' })
  })

  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null)

  useEffect(() => {
    if (loader.data) {
      setCatalog(loader.data)
    }
  }, [loader.data])

  const isLoading = loader.isLoading
  const loadError = loader.error !== null

  const langOptions = useMemo(() => {
    const langs = new Set((catalog?.voices ?? []).map(v => v.language).filter(Boolean))

    return ['', ...Array.from(langs).sort()]
  }, [catalog])

  const filteredVoices = useMemo(() => {
    const voices = catalog?.voices ?? []

    return voices.filter(v => {
      const langOk = !langFilter || v.language === langFilter || v.language === 'multi'
      const genderOk = !genderFilter || v.gender === genderFilter

      return langOk && genderOk
    })
  }, [catalog, langFilter, genderFilter])

  const preview = async (voice: VoiceOption) => {
    if (previewingId) {
      return
    }

    setPreviewingId(voice.id)

    try {
      const res = await window.spiritagent.media.tts({
        text: PREVIEW_LINE,
        voice: voice.id,
        context: 'gallery.preview',
        persist: true
      })

      const audio = new Audio(res.dataUrl)
      audio.addEventListener('ended', () => setPreviewingId(null), { once: true })
      audio.addEventListener('error', () => setPreviewingId(null), { once: true })
      await audio.play()
    } catch (err) {
      setPreviewingId(null)
      notifyError(err, t.error)
    }
  }

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingState label={t.loading} />
      </SettingsContent>
    )
  }

  if (loadError || !catalog) {
    return (
      <SettingsContent>
        <EmptyState description={t.error} title={t.error} />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <SettingsSubsection intro={t.intro} title={t.title}>
        <div className="flex flex-wrap items-center gap-1.5">
          {langOptions.map(lang => (
            <FilterPill active={langFilter === lang} key={lang || 'all'} onClick={() => setLangFilter(lang)}>
              {lang ? (LANGUAGE_LABELS[lang] ?? lang) : t.all}
            </FilterPill>
          ))}
          <span className="mx-1 text-white/20">|</span>
          {GENDER_OPTIONS.map(g => (
            <FilterPill active={genderFilter === g.id} key={g.id} onClick={() => setGenderFilter(g.id)}>
              {g.label}
            </FilterPill>
          ))}
          {catalog.provider && <Pill>{`${t.provider}: ${catalog.provider}`}</Pill>}
          {catalog.supports_voice_design && <Pill tone="primary">{t.designSupported}</Pill>}
        </div>

        <div className="mt-4 space-y-1.5">
          {filteredVoices.length === 0 ? (
            <EmptyState description={t.empty} title={t.empty} />
          ) : (
            filteredVoices.map(v => (
              <ListRow
                action={
                  <Button
                    className="border-white/15 bg-white/5 text-white/80 hover:bg-white/15 hover:text-white"
                    disabled={previewingId !== null}
                    onClick={() => void preview(v)}
                    size="sm"
                    variant="outline"
                  >
                    {previewingId === v.id ? t.playing : t.preview}
                  </Button>
                }
                description={
                  <span className="flex flex-wrap gap-1">
                    {v.tags.map(tag => (
                      <Pill key={tag}>{tag}</Pill>
                    ))}
                  </span>
                }
                key={v.id}
                title={
                  <span className="flex items-center gap-2">
                    {v.label}
                    <span className="text-[10px] font-normal text-white/40">
                      {LANGUAGE_LABELS[v.language] ?? v.language}
                    </span>
                  </span>
                }
              />
            ))
          )}
        </div>
      </SettingsSubsection>
    </SettingsContent>
  )
}
