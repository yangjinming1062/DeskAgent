import type { SpiritAgentGateway } from '@/shared/spiritagent'

export type SettingsView = 'about' | 'account' | 'mcp' | 'models' | 'runner' | 'skills' | 'speech' | 'voices'

export interface SettingsPageProps {
  gateway?: SpiritAgentGateway | null
  onClose: () => void
  onConfigSaved?: () => void
}
