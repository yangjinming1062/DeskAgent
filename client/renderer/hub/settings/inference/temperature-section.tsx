import { Slider } from '@/shared/panel'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

type TemperatureCopy = (typeof strings)['settings']['inference']['temperature']

export interface TemperatureFormState {
  chat_temperature: number
  title_generation_temperature: number
  compression_temperature: number
}

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
      <ListRow
        action={
          <div className="flex w-full items-center gap-3">
            <Slider
              ariaLabel={t.chatTemperature}
              disabled={disabled}
              max={1}
              min={0}
              onChange={value => update({ chat_temperature: Math.round(value * 100) / 100 })}
              step={0.01}
              value={state.chat_temperature}
            />
            <span className="w-8 shrink-0 text-right font-mono text-xs text-white/70">
              {state.chat_temperature.toFixed(2)}
            </span>
          </div>
        }
        description={t.chatTemperatureDesc}
        title={t.chatTemperature}
      />
      <ListRow
        action={
          <div className="flex w-full items-center gap-3">
            <Slider
              ariaLabel={t.titleTemperature}
              disabled={disabled}
              max={1}
              min={0}
              onChange={value => update({ title_generation_temperature: Math.round(value * 100) / 100 })}
              step={0.01}
              value={state.title_generation_temperature}
            />
            <span className="w-8 shrink-0 text-right font-mono text-xs text-white/70">
              {state.title_generation_temperature.toFixed(2)}
            </span>
          </div>
        }
        description={t.titleTemperatureDesc}
        title={t.titleTemperature}
      />
      <ListRow
        action={
          <div className="flex w-full items-center gap-3">
            <Slider
              ariaLabel={t.compressionTemperature}
              disabled={disabled}
              max={1}
              min={0}
              onChange={value => update({ compression_temperature: Math.round(value * 100) / 100 })}
              step={0.01}
              value={state.compression_temperature}
            />
            <span className="w-8 shrink-0 text-right font-mono text-xs text-white/70">
              {state.compression_temperature.toFixed(2)}
            </span>
          </div>
        }
        description={t.compressionTemperatureDesc}
        title={t.compressionTemperature}
      />
    </SettingsSubsection>
  )
}
