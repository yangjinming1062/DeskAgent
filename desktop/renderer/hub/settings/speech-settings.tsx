import { IconVolume } from '@tabler/icons-react'
import { useEffect, useState } from 'react'

import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { SegmentedControl } from '@/shared/components/ui/segmented-control'
import type { SegmentedControlOption } from '@/shared/components/ui/segmented-control'
import { Switch } from '@/shared/components/ui/switch'
import { getDeskAgentConfig, saveDeskAgentConfig } from '@/shared/deskagent/config'
import { triggerHaptic } from '@/shared/lib/haptics'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { SpeechEngine } from '@/shared/types/deskagent'
import type { DeskAgentConfigResponse } from '@/shared/types/deskagent'

import { ListRow, LoadingState, Pill, SettingsContent, SettingsSubsection } from './primitives'

interface SpeechFormState {
  sttEnabled: boolean
  sttEngine: SpeechEngine
  sttSilentFallback: boolean
  ttsEngine: SpeechEngine
  maxRecordingSeconds: number
}

const DEFAULTS: SpeechFormState = {
  sttEnabled: true,
  sttEngine: 'auto',
  sttSilentFallback: true,
  ttsEngine: 'auto',
  maxRecordingSeconds: 60
}

const readState = (config: DeskAgentConfigResponse): SpeechFormState => ({
  sttEnabled: config.stt?.enabled ?? DEFAULTS.sttEnabled,
  sttEngine: config.stt?.engine ?? DEFAULTS.sttEngine,
  sttSilentFallback: config.stt?.silent_fallback ?? DEFAULTS.sttSilentFallback,
  ttsEngine: config.tts?.engine ?? DEFAULTS.ttsEngine,
  maxRecordingSeconds: config.voice?.max_recording_seconds ?? DEFAULTS.maxRecordingSeconds
})

export function SpeechSettings() {
  const s = strings.speech
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [original, setOriginal] = useState<SpeechFormState>(DEFAULTS)
  const [state, setState] = useState<SpeechFormState>(DEFAULTS)
  // Local-engine availability, probed from the Runner tool schema (null = unknown).
  const [localSttAvailable, setLocalSttAvailable] = useState<boolean | null>(null)
  const [localTtsAvailable, setLocalTtsAvailable] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const config = await getDeskAgentConfig()

        if (cancelled) {
          return
        }

        const next = readState(config)
        setOriginal(next)
        setState(next)
      } catch {
        // leave defaults on load failure
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

  // Probe which local engines the Runner currently advertises (check_fn-gated),
  // so the user can see whether "local"/"auto" will actually use a local engine.
  // Re-probe on tools_changed/running so the badge flips once the Runner finishes loading.
  useEffect(() => {
    let cancelled = false

    const probe = async () => {
      try {
        const tools = await window.deskagent.runnerGetTools?.()

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

    const off = window.deskagent.onRunnerStatus?.((ev: { type: string }) => {
      if (ev.type === 'tools_changed' || ev.type === 'running') {
        void probe()
      }
    })

    return () => {
      cancelled = true
      off?.()
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
      const { config } = await saveDeskAgentConfig({
        stt: { enabled: state.sttEnabled, engine: state.sttEngine, silent_fallback: state.sttSilentFallback },
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
    avail === null ? null : <Pill tone={avail ? 'primary' : 'muted'}>{avail ? s.engineLocalAvail : s.engineLocalUnavail}</Pill>

  return (
    <SettingsContent>
      <SettingsSubsection icon={IconVolume} intro={s.intro} title={s.title}>
        <div className="divide-y divide-(--ui-stroke-tertiary)">
          <ListRow
            action={<Switch checked={state.sttEnabled} onCheckedChange={v => update({ sttEnabled: v })} />}
            description={s.sttDesc}
            title={s.sttTitle}
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
              action={<Switch checked={state.sttSilentFallback} onCheckedChange={v => update({ sttSilentFallback: v })} />}
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
              <Input
                className="w-28"
                min={5}
                onChange={e => {
                  const v = e.target.value === '' ? NaN : Number(e.target.value)

                  update({ maxRecordingSeconds: isNaN(v) ? DEFAULTS.maxRecordingSeconds : v })
                }}
                type="number"
                value={state.maxRecordingSeconds}
              />
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
