import { IconVolume } from '@tabler/icons-react'
import { useEffect, useState } from 'react'

import { Button, SegmentedControl, type SegmentedControlOption, Switch } from '@/shared/components/ui'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { triggerHaptic } from '@/shared/lib/haptics'
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
  // Local-engine availability, probed from the Runner tool schema (null = unknown).
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

  // Probe which local engines the Runner currently advertises (check_fn-gated),
  // so the user can see whether "local"/"auto" will actually use a local engine.
  // Re-probe on runner phase transitions — `running` means the bridge is up
  // and tools have been fetched, `tools_changed` means MCP discovery finished
  // post-startup. Both surface via the shared $runnerPhase atom (currently
  // collapsed: only `running` is published, but the subscription wires up
  // the same way if the atom grows new event types).
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

  const engineOptions: readonly SegmentedControlOption<SpeechEngine>[] = [
    { id: 'auto', label: s.engineAuto },
    { id: 'local', label: s.engineLocal },
    { id: 'cloud', label: s.engineCloud }
  ]

  const availBadge = (avail: boolean | null) =>
    avail === null ? null : (
      <Pill tone={avail ? 'primary' : 'muted'}>{avail ? s.engineLocalAvail : s.engineLocalUnavail}</Pill>
    )

  // Include the current value so non-standard values still show a matching <option>.
  const recordingOptions = Array.from(new Set([...RECORDING_OPTIONS, state.maxRecordingSeconds])).sort((a, b) => a - b)

  return (
    <SettingsContent>
      <SettingsSubsection icon={IconVolume} intro={s.intro} title={s.title}>
        <div className="divide-y divide-(--ui-stroke-tertiary)">
          <ListRow
            action={<Switch checked={state.sttEnabled} onCheckedChange={v => update({ sttEnabled: v })} />}
            description={s.sttEnabledDesc}
            title={s.sttEnabledTitle}
          />
          <ListRow
            action={
              <div className="flex flex-col items-end gap-1.5">
                <SegmentedControl
                  onChange={v => update({ sttEngine: v })}
                  options={engineOptions}
                  value={state.sttEngine}
                />
                {availBadge(localSttAvailable)}
              </div>
            }
            description={s.sttEngineDesc}
            title={s.sttEngineTitle}
          />
          {state.sttEngine === 'auto' && (
            <ListRow
              action={
                <Switch checked={state.sttSilentFallback} onCheckedChange={v => update({ sttSilentFallback: v })} />
              }
              description={s.sttSilentFallbackDesc}
              title={s.sttSilentFallbackTitle}
            />
          )}
          <ListRow
            action={
              <div className="flex flex-col items-end gap-1.5">
                <SegmentedControl
                  onChange={v => update({ ttsEngine: v })}
                  options={engineOptions}
                  value={state.ttsEngine}
                />
                {availBadge(localTtsAvailable)}
              </div>
            }
            description={s.ttsEngineDesc}
            title={s.ttsEngineTitle}
          />
          <ListRow
            action={
              <select
                className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-input) px-3 py-1.5 text-sm text-foreground outline-none"
                onChange={e => update({ maxRecordingSeconds: Number(e.currentTarget.value) })}
                value={state.maxRecordingSeconds}
              >
                {recordingOptions.map(s => (
                  <option key={s} value={s}>
                    {s}s
                  </option>
                ))}
              </select>
            }
            description={s.recordingDesc}
            title={s.recordingTitle}
          />
        </div>

        <div className="mt-8 flex justify-end">
          <Button disabled={isSaving || !isDirty} onClick={() => void save()}>
            {isSaving ? s.saving : s.save}
          </Button>
        </div>
      </SettingsSubsection>
    </SettingsContent>
  )
}
