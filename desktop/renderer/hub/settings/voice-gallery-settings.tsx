import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/shared/components/ui/button'
import { AudioLines } from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { EmptyState, ListRow, LoadingState, Pill, SettingsContent, SettingsSubsection } from './primitives'

// Read-only voice catalog browser for the framed tool window (hub), which has
// no WS gateway and thus can't call the `tts.list_voices` JSON-RPC method. It
// reaches the same backend catalog via REST (GET /api/companion/voices) and
// previews a voice through the `deskagent:media:tts` IPC (available to both
// windows). Changing the active companion voice stays in the sprite window's
// 伙伴设置 — this page only browses + 试听.
//
// Types are defined locally rather than imported from @/companion/voice: the
// hub↔companion ESLint boundary forbids direct imports, and a viewer needs
// only a sliver of the catalog shape.

interface VoiceOption {
  id: string
  label: string
  gender: string
  language: string
  tags: readonly string[]
  description: string
}

interface VoiceCatalog {
  provider: string
  voices: VoiceOption[]
  supports_voice_design?: boolean
}

const LANGUAGE_LABELS: Record<string, string> = {
  zh: '中文',
  en: '英文',
  multi: '多语言',
  '': '通用'
}

const GENDER_OPTIONS: { id: string; label: string }[] = [
  { id: '', label: '全部' },
  { id: 'female', label: '女声' },
  { id: 'male', label: '男声' },
  { id: 'neutral', label: '中性' }
]

const PREVIEW_LINE = '你好呀，这是我的声音～'

export function VoiceGallerySettings() {
  const t = strings.voiceGallery
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [langFilter, setLangFilter] = useState('')
  const [genderFilter, setGenderFilter] = useState('')
  const [previewingId, setPreviewingId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const res = await window.deskagent.api<VoiceCatalog>({ path: '/api/companion/voices' })

        if (!cancelled) {
          setCatalog(res)
          setLoadError(false)
        }
      } catch {
        if (!cancelled) {
          setLoadError(true)
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

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
    if (previewingId) {return}
    setPreviewingId(voice.id)

    try {
      const res = await window.deskagent.media.tts({ text: PREVIEW_LINE, voice: voice.id, context: 'gallery.preview' })
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
      <SettingsSubsection icon={AudioLines} intro={t.intro} title={t.title}>
        <div className="flex flex-wrap items-center gap-1.5">
          {langOptions.map(lang => (
            <button
              className={`rounded-full px-2.5 py-0.5 text-xs transition ${langFilter === lang ? 'bg-(--ui-bg-tertiary) font-medium text-foreground' : 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'}`}
              key={lang || 'all'}
              onClick={() => setLangFilter(lang)}
              type="button"
            >
              {lang ? (LANGUAGE_LABELS[lang] ?? lang) : t.all}
            </button>
          ))}
          <span className="mx-1 text-(--ui-stroke-tertiary)">|</span>
          {GENDER_OPTIONS.map(g => (
            <button
              className={`rounded-full px-2.5 py-0.5 text-xs transition ${genderFilter === g.id ? 'bg-(--ui-bg-tertiary) font-medium text-foreground' : 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'}`}
              key={g.id}
              onClick={() => setGenderFilter(g.id)}
              type="button"
            >
              {g.label}
            </button>
          ))}
          {catalog.provider && <Pill>{`${t.provider}: ${catalog.provider}`}</Pill>}
          {catalog.supports_voice_design && <Pill tone="primary">{t.designSupported}</Pill>}
        </div>

        <div className="mt-4 divide-y divide-(--ui-stroke-tertiary)">
          {filteredVoices.length === 0 ? (
            <EmptyState description={t.empty} title={t.empty} />
          ) : (
            filteredVoices.map(v => (
              <ListRow
                action={
                  <Button disabled={previewingId !== null} onClick={() => void preview(v)} size="sm" variant="outline">
                    {previewingId === v.id ? t.playing : t.preview}
                  </Button>
                }
                description={<span className="flex flex-wrap gap-1">{v.tags.map(tag => <Pill key={tag}>{tag}</Pill>)}</span>}
                key={v.id}
                title={
                  <span className="flex items-center gap-2">
                    {v.label}
                    <span className="text-xs font-normal text-(--ui-text-tertiary)">{LANGUAGE_LABELS[v.language] ?? v.language}</span>
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
