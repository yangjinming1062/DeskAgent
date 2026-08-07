// Single source of truth for the three disturbance tiers. The id matches the
// `DisturbanceTier` union in companion-store.ts. The `label` is the user-visible
// chip text; the optional `hint` is a longer one-line description used in
// settings surfaces (chat-dock's compact chip omits it). Order is the
// display order: most permissive → most restrictive.

import type { DisturbanceTier } from './companion-store'

export interface DisturbanceTierOption {
  hint: string
  id: DisturbanceTier
  label: string
}

export const DISTURBANCE_TIERS: readonly DisturbanceTierOption[] = [
  { id: 'proactive', label: '积极主动', hint: '语音、气泡、主动消息全开放' },
  { id: 'normal', label: '常规', hint: '仅轻量文字消息' },
  { id: 'quiet', label: '保持安静', hint: '不发消息，但情绪仍流动' }
] as const
