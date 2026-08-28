export type SettingsView = 'about' | 'appearance' | 'channels' | 'inference' | 'runner' | 'skills' | 'speech'

export interface SettingsPageProps {
  onClose: () => void
  onConfigSaved?: () => void
}
