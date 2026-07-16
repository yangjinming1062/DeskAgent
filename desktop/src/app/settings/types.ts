import type { ZastGateway } from '@/zast'

export type SettingsView = 'about' | 'account' | 'mcp' | 'sessions' | 'appearance' | 'runner' | 'skills'

export interface SettingsPageProps {
  gateway?: ZastGateway | null
  onClose: () => void
  onConfigSaved?: () => void
}
