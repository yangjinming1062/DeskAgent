import { ListRow, SettingsSubsection, Slider } from '@/shared/panel'
import type { strings } from '@/shared/strings'

type TemperatureCopy = (typeof strings)['settings']['inference']['temperature']

export interface TemperatureFormState {
  chat_temperature: number
  compression_temperature: number
  title_generation_temperature: number
}

const FIELDS: ReadonlyArray<{
  descKey: 'chatTemperatureDesc' | 'compressionTemperatureDesc' | 'titleTemperatureDesc'
  field: keyof TemperatureFormState
  labelKey: 'chatTemperature' | 'compressionTemperature' | 'titleTemperature'
}> = [
  { field: 'chat_temperature', labelKey: 'chatTemperature', descKey: 'chatTemperatureDesc' },
  { field: 'title_generation_temperature', labelKey: 'titleTemperature', descKey: 'titleTemperatureDesc' },
  { field: 'compression_temperature', labelKey: 'compressionTemperature', descKey: 'compressionTemperatureDesc' }
]

export function TemperatureSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: TemperatureFormState
  t: TemperatureCopy
  update: (patch: Partial<TemperatureFormState>) => void
}): React.JSX.Element {
  return (
    <SettingsSubsection intro={t.intro} title={t.heading}>
      {FIELDS.map(({ field, labelKey, descKey }) => (
        <ListRow
          action={
            <div className="flex w-full items-center gap-3">
              <Slider
                ariaLabel={t[labelKey]}
                disabled={disabled}
                max={1}
                min={0}
                onChange={value => update({ [field]: Math.round(value * 100) / 100 } as Partial<TemperatureFormState>)}
                step={0.01}
                value={state[field]}
              />
              <span className="w-8 shrink-0 text-right font-mono text-xs text-body">{state[field].toFixed(2)}</span>
            </div>
          }
          description={t[descKey]}
          key={field}
          title={t[labelKey]}
        />
      ))}
    </SettingsSubsection>
  )
}
