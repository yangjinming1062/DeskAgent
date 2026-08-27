import { useEffect, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { triggerHaptic } from '@/shared/lib/haptics'
import { BTN_PRIMARY, PanelSelect, Segmented, Toggle } from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notify, notifyError } from '@/shared/store/notifications'
import { $runnerPhase } from '@/shared/store/runner-status'
import { strings } from '@/shared/strings'
import type { SpeechEngine } from '@/shared/types/spiritagent'
import type { SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

import { ListRow, LoadingState, Pill, SettingsContent, SettingsSubsection } from './primitives'

interface SpeechFormState {
  sttEnabled: boolean
  sttEngine: SpeechEngine
  sttSilentFallback: boolean
  ttsEngine: SpeechEngine
  maxRecordingSeconds: number
}

const RECORDING_OPTIONS = [15, 30, 60, 120, 300] as const

const DEFAULTS: SpeechFormState = {
  sttEnabled: true,
  sttEngine: 'auto',
  sttSilentFallback: true,
  ttsEngine: 'auto',
  maxRecordingSeconds: 60
}

const readState = (config: SpiritAgentConfigResponse): SpeechFormState => ({
  sttEnabled: config.stt?.enabled ?? DEFAULTS.sttEnabled,
  sttEngine: config.stt?.engine ?? DEFAULTS.sttEngine,
  sttSilentFallback: config.stt?.silent_fallback ?? DEFAULTS.sttSilentFallback,
  ttsEngine: config.tts?.engine ?? DEFAULTS.ttsEngine,
  maxRecordingSeconds: config.voice?.max_recording_seconds ?? DEFAULTS.maxRecordingSeconds
})

export function SpeechSettings(): React.JSX.Element {
  const s = strings.speech
  const configLoader = useAsyncLoader<SpiritAgentConfigResponse>(() => getSpiritAgentConfig())
  const [isSaving, setIsSaving] = useState(false)
  const [original, setOriginal] = useState<SpeechFormState>(DEFAULTS)
  const [state, setState] = useState<SpeechFormState>(DEFAULTS)
  // 本地引擎可用性（null = 未知），从 Runner 工具 schema 探测
  const [localSttAvailable, setLocalSttAvailable] = useState<boolean | null>(null)
  const [localTtsAvailable, setLocalTtsAvailable] = useState<boolean | null>(null)

  useEffect(() => {
    if (configLoader.data) {
      const next = readState(configLoader.data)
      setOriginal(next)
      setState(next)
    }
  }, [configLoader.data])

  const isLoading = configLoader.isLoading

  // 探测 Runner 当前可用的本地引擎（check_fn 门控），让用户知道 "local"/"auto" 是否会真正使用本地引擎。
  // 在 Runner 阶段切换时重新探测——`running` 表示 bridge 已就绪。
  useEffect(() => {
    let cancelled = false

    const probe = async () => {
      try {
        const tools = await window.spiritagent.runnerGetTools?.()

        if (cancelled || !Array.isArray(tools)) {
          return
        }

        const names = new Set(
          (tools as Array<{ function?: { name?: string }; name?: string }>)
            .map(t => t?.function?.name || t?.name)
            .filter((n): n is string => Boolean(n))
        )

        setLocalSttAvailable(names.has('speech_to_text'))
        setLocalTtsAvailable(names.has('text_to_speech'))
      } catch {
        // leave null — availability badge stays hidden
      }
    }

    void probe()

    const off = $runnerPhase.subscribe(phase => {
      if (phase === 'running') {
        void probe()
      }
    })

    return () => {
      cancelled = true
      off()
    }
  }, [])

  const isDirty =
    state.sttEnabled !== original.sttEnabled ||
    state.sttEngine !== original.sttEngine ||
    state.sttSilentFallback !== original.sttSilentFallback ||
    state.ttsEngine !== original.ttsEngine ||
    state.maxRecordingSeconds !== original.maxRecordingSeconds

  const update = (patch: Partial<SpeechFormState>) => {
    setState(prev => ({ ...prev, ...patch }))
  }

  const save = async () => {
    setIsSaving(true)

    try {
      const { config } = await saveSpiritAgentConfig({
        stt: {
          enabled: state.sttEnabled,
          engine: state.sttEngine,
          silent_fallback: state.sttSilentFallback
        },
        tts: { engine: state.ttsEngine },
        voice: { max_recording_seconds: state.maxRecordingSeconds }
      })

      const next = readState(config)
      setOriginal(next)
      setState(next)
      triggerHaptic('success')
      notify({ kind: 'success', message: s.saved })
    } catch (err) {
      notifyError(err, s.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingState label={s.loading} />
      </SettingsContent>
    )
  }

  // STT auto routes local-first (free), TTS auto routes cloud-first (better
  // voice quality) — the "auto" labels must state each engine's own priority.
  const sttEngineOptions: readonly { id: SpeechEngine; label: string }[] = [
    { id: 'auto', label: s.sttEngineAuto },
    { id: 'local', label: s.engineLocal },
    { id: 'cloud', label: s.engineCloud }
  ]

  const ttsEngineOptions: readonly { id: SpeechEngine; label: string }[] = [
    { id: 'auto', label: s.ttsEngineAuto },
    { id: 'local', label: s.engineLocal },
    { id: 'cloud', label: s.engineCloud }
  ]

  const availBadge = (avail: boolean | null) =>
    avail === null ? null : (
      <Pill tone={avail ? 'primary' : 'muted'}>{avail ? s.engineLocalAvail : s.engineLocalUnavail}</Pill>
    )

  // 包含当前值，确保非标准值也能匹配选项
  const recordingOptions = Array.from(new Set([...RECORDING_OPTIONS, state.maxRecordingSeconds])).sort((a, b) => a - b)

  return (
    <SettingsContent>
      <SettingsSubsection intro={s.intro} title={s.title}>
        <ListRow
          action={<Toggle checked={state.sttEnabled} onChange={v => update({ sttEnabled: v })} />}
          description={s.sttEnabledDesc}
          title={s.sttEnabledTitle}
        />
        <ListRow
          action={
            <div className="flex flex-col items-end gap-1.5">
              <div className="w-44">
                <Segmented<SpeechEngine>
                  onChange={v => update({ sttEngine: v })}
                  options={sttEngineOptions.map(opt => ({ value: opt.id, label: opt.label }))}
                  value={state.sttEngine}
                />
              </div>
              {availBadge(localSttAvailable)}
            </div>
          }
          description={s.sttEngineDesc}
          title={s.sttEngineTitle}
        />
        {state.sttEngine === 'auto' && (
          <ListRow
            action={<Toggle checked={state.sttSilentFallback} onChange={v => update({ sttSilentFallback: v })} />}
            description={s.sttSilentFallbackDesc}
            title={s.sttSilentFallbackTitle}
          />
        )}
        <ListRow
          action={
            <div className="flex flex-col items-end gap-1.5">
              <div className="w-44">
                <Segmented<SpeechEngine>
                  onChange={v => update({ ttsEngine: v })}
                  options={ttsEngineOptions.map(opt => ({ value: opt.id, label: opt.label }))}
                  value={state.ttsEngine}
                />
              </div>
              {availBadge(localTtsAvailable)}
            </div>
          }
          description={s.ttsEngineDesc}
          title={s.ttsEngineTitle}
        />
        <ListRow
          action={
            <PanelSelect
              onChange={v => update({ maxRecordingSeconds: Number(v) })}
              options={recordingOptions.map(sec => ({ value: String(sec), label: `${sec}s` }))}
              value={String(state.maxRecordingSeconds)}
              widthClass="w-28"
            />
          }
          description={s.recordingDesc}
          title={s.recordingTitle}
        />

        <div className="mt-8 flex justify-end">
          <button className={BTN_PRIMARY} disabled={isSaving || !isDirty} onClick={() => void save()} type="button">
            {isSaving ? s.saving : s.save}
          </button>
        </div>
      </SettingsSubsection>
    </SettingsContent>
  )
}
