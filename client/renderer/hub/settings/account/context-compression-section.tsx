import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@/shared/components/ui'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

type ContextCompressionCopy = (typeof strings)['settings']['account']['contextCompression']

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
          <Switch
            checked={state.enable_context_compression}
            disabled={disabled}
            onCheckedChange={value => update({ enable_context_compression: value })}
          />
        }
        description={t.enableCompressionDesc}
        title={t.enableCompression}
      />
      <ListRow
        action={
          <Select
            disabled={disabled}
            onValueChange={value => update({ context_compression_threshold: Number(value) })}
            value={String(state.context_compression_threshold)}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {THRESHOLD_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.thresholdOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.thresholdDesc}
        title={t.threshold}
      />
    </SettingsSubsection>
  )
}
