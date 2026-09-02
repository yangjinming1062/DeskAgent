import { useEffect, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { triggerHaptic } from '@/shared/lib/haptics'
import { BTN_PRIMARY, LoadingBlock, PanelSelect, Toggle } from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

import { ListRow, SettingsContent, SettingsSubsection } from './primitives'

interface SpeechFormState {
  sttEnabled: boolean
  maxRecordingSeconds: number
}

const RECORDING_OPTIONS = [15, 30, 60, 120, 300] as const

const DEFAULTS: SpeechFormState = {
  sttEnabled: true,
  maxRecordingSeconds: 60
}

const readState = (config: SpiritAgentConfigResponse): SpeechFormState => ({
  sttEnabled: config.stt?.enabled ?? DEFAULTS.sttEnabled,
  maxRecordingSeconds: config.voice?.max_recording_seconds ?? DEFAULTS.maxRecordingSeconds
})

export function SpeechSettings(): React.JSX.Element {
  const s = strings.speech
  const configLoader = useAsyncLoader<SpiritAgentConfigResponse>(() => getSpiritAgentConfig())
  const [isSaving, setIsSaving] = useState(false)
  const [original, setOriginal] = useState<SpeechFormState>(DEFAULTS)
  const [state, setState] = useState<SpeechFormState>(DEFAULTS)

  useEffect(() => {
    if (configLoader.data) {
      const next = readState(configLoader.data)
      setOriginal(next)
      setState(next)
    }
  }, [configLoader.data])

  const isLoading = configLoader.isLoading

  const isDirty = state.sttEnabled !== original.sttEnabled || state.maxRecordingSeconds !== original.maxRecordingSeconds

  const update = (patch: Partial<SpeechFormState>) => {
    setState(prev => ({ ...prev, ...patch }))
  }

  const save = async () => {
    setIsSaving(true)

    try {
      const { config } = await saveSpiritAgentConfig({
        stt: { enabled: state.sttEnabled },
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
        <LoadingBlock label={s.loading} />
      </SettingsContent>
    )
  }

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
