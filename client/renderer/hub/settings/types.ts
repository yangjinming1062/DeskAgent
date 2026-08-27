export type SettingsView = 'about' | 'account' | 'runner' | 'skills' | 'speech'

export interface SettingsPageProps {
  onClose: () => void
  onConfigSaved?: () => void
}
