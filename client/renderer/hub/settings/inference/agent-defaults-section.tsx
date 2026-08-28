import { PanelSelect, Toggle } from '@/shared/panel'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

type AgentDefaultsCopy = (typeof strings)['settings']['inference']['agentDefaults']

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
          <PanelSelect
            disabled={disabled}
            onChange={value => update({ reasoning_effort: value })}
            options={REASONING_OPTIONS.map(opt => ({ value: opt, label: t.reasoningOptions[opt] }))}
            value={state.reasoning_effort}
          />
        }
        description={t.reasoningEffortDesc}
        title={t.reasoningEffort}
      />

      <ListRow
        action={
          <Toggle
            checked={state.enable_background_review}
            disabled={disabled}
            onChange={value => update({ enable_background_review: value })}
          />
        }
        description={t.backgroundReviewDesc}
        title={t.backgroundReview}
      />
    </SettingsSubsection>
  )
}
