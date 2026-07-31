import type { DeskAgentGateway } from '@/shared/deskagent'

export type SettingsView = 'about' | 'account' | 'mcp' | 'sessions' | 'runner' | 'skills' | 'speech'

export interface SettingsPageProps {
  gateway?: DeskAgentGateway | null
  onClose: () => void
  onConfigSaved?: () => void
}
