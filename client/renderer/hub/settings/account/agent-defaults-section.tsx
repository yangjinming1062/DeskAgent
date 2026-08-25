import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@/shared/components/ui'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

export type AgentDefaultsCopy = (typeof strings)['settings']['account']['agentDefaults']

export interface AgentFormState {
  reasoning_effort: string
  enable_background_review: boolean
}

const REASONING_OPTIONS = ['none', 'low', 'medium', 'high'] as const

export function AgentDefaultsSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: AgentFormState
  t: AgentDefaultsCopy
  update: (patch: Partial<AgentFormState>) => void
}): React.JSX.Element {
  return (
    <SettingsSubsection intro={t.intro} title={t.heading}>
      <ListRow
        action={
          <Select
            disabled={disabled}
            onValueChange={value => update({ reasoning_effort: value })}
            value={state.reasoning_effort}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REASONING_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.reasoningOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.reasoningEffortDesc}
        title={t.reasoningEffort}
      />

      <ListRow
        action={
          <Switch
            checked={state.enable_background_review}
            disabled={disabled}
            onCheckedChange={value => update({ enable_background_review: value })}
          />
        }
        description={t.backgroundReviewDesc}
        title={t.backgroundReview}
      />
    </SettingsSubsection>
  )
}
