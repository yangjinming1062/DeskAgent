export type SettingsView = 'about' | 'account' | 'runner' | 'skills' | 'speech' | 'voices'

export interface SettingsPageProps {
  onClose: () => void
  onConfigSaved?: () => void
}
