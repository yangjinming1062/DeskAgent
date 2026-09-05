import { useStore } from '@nanostores/react'
import { IconCheck } from '@tabler/icons-react'
import type React from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { cn } from '@/shared/lib/utils'
import { SettingCard } from '@/shared/panel'
import { $theme, setUiTheme } from '@/shared/store/theme'
import { strings } from '@/shared/strings'
import { type ThemeDefinition, THEMES } from '@/shared/theme/registry'

function ThemePreview({ preview }: { preview: ThemeDefinition['preview'] }): React.JSX.Element {
  return (
    <span className="flex h-10 w-[4.5rem] shrink-0 overflow-hidden rounded-lg border border-line-standard">
      <span className="flex flex-1 flex-col">
        <span className="flex-1" style={{ background: preview.chrome }} />
        <span className="flex-1" style={{ background: preview.panel }} />
        <span className="flex-1" style={{ background: preview.card }} />
      </span>
      <span className="w-2.5" style={{ background: preview.accent }} />
    </span>
  )
}

export function ThemePage(): React.JSX.Element {
  const a = strings.settings.appearance
  const active = useStore($theme)

  return (
    <div>
      <div className="pt-1 pb-4">
        <h2 className="text-sm font-semibold text-strong">{a.heading}</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-faint">{a.hint}</p>
      </div>
      <SettingCard>
        {THEMES.map(theme => {
          const isActive = theme.id === active

          return (
            <button
              className={cn(
                'flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left transition',
                isActive ? 'bg-accent-soft' : 'hover:bg-fill-hover'
              )}
              key={theme.id}
              onClick={() => {
                triggerHaptic('open')
                setUiTheme(theme.id)
              }}
              type="button"
            >
              <span className="min-w-0">
                <span className="flex items-center gap-1.5 text-xs font-medium text-strong">
                  {isActive && <IconCheck className="size-3.5 shrink-0 text-accent" />}
                  {theme.label}
                </span>
                <span className="mt-0.5 block text-[11px] leading-relaxed text-faint">{theme.description}</span>
              </span>
              <ThemePreview preview={theme.preview} />
            </button>
          )
        })}
      </SettingCard>
    </div>
  )
}
