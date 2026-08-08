import type { DeskAgentGateway } from '@/shared/deskagent'

export type SettingsView = 'about' | 'account' | 'mcp' | 'models' | 'runner' | 'skills' | 'speech' | 'voices'

export interface SettingsPageProps {
  gateway?: DeskAgentGateway | null
  onClose: () => void
  onConfigSaved?: () => void
}
