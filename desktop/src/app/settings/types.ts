import type { DeskAgentGateway } from '@/deskagent'

export type SettingsView = 'about' | 'account' | 'mcp' | 'sessions' | 'appearance' | 'runner' | 'skills'

export interface SettingsPageProps {
  gateway?: DeskAgentGateway | null
  onClose: () => void
  onConfigSaved?: () => void
}
