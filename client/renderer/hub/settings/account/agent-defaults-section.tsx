import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@/shared/components/ui'
import { SlidersHorizontal } from '@/shared/lib/icons'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

export type AgentDefaultsCopy = (typeof strings)['settings']['account']['agentDefaults']

export interface AgentFormState {
  reasoning_effort: string
  service_tier: string
  enable_background_review: boolean
}

const REASONING_OPTIONS = ['minimal', 'low', 'medium', 'high', 'max'] as const
// OpenAI service_tier 仅接受 {auto, default, flex}，UI 必须与 API 允许集一致
const SERVICE_TIER_OPTIONS = ['auto', 'default', 'flex'] as const

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
    <SettingsSubsection icon={SlidersHorizontal} intro={t.intro} title={t.heading}>
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
          <Select
            disabled={disabled}
            onValueChange={value => update({ service_tier: value })}
            value={state.service_tier}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SERVICE_TIER_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.serviceTierOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.serviceTierDesc}
        title={t.serviceTier}
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
