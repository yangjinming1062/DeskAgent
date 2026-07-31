import { IconVolume } from '@tabler/icons-react'
import { useEffect, useState } from 'react'

import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Switch } from '@/shared/components/ui/switch'
import { getDeskAgentConfig, saveDeskAgentConfig } from '@/shared/deskagent/config'
import { triggerHaptic } from '@/shared/lib/haptics'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { DeskAgentConfigResponse } from '@/shared/types/deskagent'

import { ListRow, LoadingState, SettingsContent, SettingsSubsection } from './primitives'

interface SpeechFormState {
  sttEnabled: boolean
  maxRecordingSeconds: number
}

const DEFAULTS: SpeechFormState = {
  sttEnabled: true,
  maxRecordingSeconds: 60
}

const readState = (config: DeskAgentConfigResponse): SpeechFormState => ({
  sttEnabled: config.stt?.enabled ?? DEFAULTS.sttEnabled,
  maxRecordingSeconds: config.voice?.max_recording_seconds ?? DEFAULTS.maxRecordingSeconds
})

export function SpeechSettings() {
  const s = strings.speech
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [original, setOriginal] = useState<SpeechFormState>(DEFAULTS)
  const [state, setState] = useState<SpeechFormState>(DEFAULTS)

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

  const isDirty = state.sttEnabled !== original.sttEnabled || state.maxRecordingSeconds !== original.maxRecordingSeconds

  const update = (patch: Partial<SpeechFormState>) => {
    setState(prev => ({ ...prev, ...patch }))
  }

  const save = async () => {
    setIsSaving(true)

    try {
      const { config } = await saveDeskAgentConfig({
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
        <LoadingState label={s.loading} />
      </SettingsContent>
    )
  }

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
