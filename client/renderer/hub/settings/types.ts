export type SettingsTab =
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
}
