export type SettingsView =
  | 'about'
  | 'appearance'
  | 'channels'
  | 'inference'
  | 'runner'
  | 'shortcuts'
  | 'skills'
  | 'speech'

export interface SettingsPageProps {
  onClose: () => void
  onConfigSaved?: () => void
}
