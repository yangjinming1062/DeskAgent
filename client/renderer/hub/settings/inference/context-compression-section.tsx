import { Slider, Toggle } from '@/shared/panel'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

type ContextCompressionCopy = (typeof strings)['settings']['inference']['contextCompression']

export interface ChatFormState {
  enable_context_compression: boolean
  context_compression_threshold: number
}

const THRESHOLD_MIN = 0.3
const THRESHOLD_MAX = 1.0
const THRESHOLD_STEP = 0.05

export function ContextCompressionSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: ChatFormState
  t: ContextCompressionCopy
  update: (patch: Partial<ChatFormState>) => void
}): React.JSX.Element {
  const thresholdPct = Math.round(state.context_compression_threshold * 100)

  return (
    <SettingsSubsection intro={t.intro} title={t.heading}>
      <ListRow
        action={
          <Toggle
            checked={state.enable_context_compression}
            disabled={disabled}
            onChange={value => update({ enable_context_compression: value })}
          />
        }
        description={t.enableCompressionDesc}
        title={t.enableCompression}
      />
      <ListRow
        action={
          <div className="flex w-full items-center gap-3">
            <Slider
              ariaLabel={t.threshold}
              disabled={disabled}
              max={THRESHOLD_MAX}
              min={THRESHOLD_MIN}
              onChange={value =>
                update({
                  context_compression_threshold: Math.min(
                    THRESHOLD_MAX,
                    Math.max(THRESHOLD_MIN, Math.round(value * 100) / 100)
                  )
                })
              }
              step={THRESHOLD_STEP}
              value={state.context_compression_threshold}
            />
            <span className="w-12 shrink-0 text-right font-mono text-xs text-white/70">{thresholdPct}%</span>
          </div>
        }
        description={t.thresholdDesc}
        title={t.threshold}
      />
    </SettingsSubsection>
  )
}
