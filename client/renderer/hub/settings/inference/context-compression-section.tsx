import { PanelSelect, Toggle } from '@/shared/panel'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

type ContextCompressionCopy = (typeof strings)['settings']['inference']['contextCompression']

export interface ChatFormState {
  enable_context_compression: boolean
  context_compression_threshold: number
}

const THRESHOLD_OPTIONS = ['0.5', '0.6', '0.7', '0.8', '0.9'] as const

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
          <PanelSelect
            disabled={disabled}
            onChange={value => update({ context_compression_threshold: Number(value) })}
            options={THRESHOLD_OPTIONS.map(opt => ({ value: opt, label: t.thresholdOptions[opt] }))}
            value={String(state.context_compression_threshold)}
          />
        }
        description={t.thresholdDesc}
        title={t.threshold}
      />
    </SettingsSubsection>
  )
}
