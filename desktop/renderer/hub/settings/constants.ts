import { type IconComponent, Monitor, Moon, Sun } from '@/shared/lib/icons'
import type { ThemeMode } from '@/shared/themes/context'

export const EMPTY_SELECT_VALUE = '__deskagent_empty__'
export const UNCATEGORIZED_KEY = '__uncategorized__'
export const CONTROL_TEXT = 'text-xs'

export const BUILTIN_PERSONALITIES = [
  'helpful',
  'concise',
  'technical',
  'creative',
  'teacher',
  'kawaii',
  'catgirl',
  'pirate',
  'shakespeare',
  'surfer',
  'noir',
  'uwu',
  'philosopher',
  'hype'
]

export interface ModeOption {
  id: ThemeMode
  label: string
  icon: IconComponent
}

export const MODE_OPTIONS: ModeOption[] = [
  { id: 'light', label: 'Light', icon: Sun },
  { id: 'dark', label: 'Dark', icon: Moon },
  { id: 'system', label: 'System', icon: Monitor }
]
