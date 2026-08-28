export type SettingsView = 'about' | 'account' | 'appearance' | 'channels' | 'runner' | 'skills' | 'speech'

export interface SettingsPageProps {
  onClose: () => void
  onConfigSaved?: () => void
}
