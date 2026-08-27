export type SettingsView = 'about' | 'account' | 'appearance' | 'runner' | 'skills' | 'speech'

export interface SettingsPageProps {
  onClose: () => void
  onConfigSaved?: () => void
}
