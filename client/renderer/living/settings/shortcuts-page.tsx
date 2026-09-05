import { DEFAULT_SHORTCUTS, type DesktopShortcutsConfig, type DesktopShortcutsState } from '@ipc/contracts'
import type React from 'react'
import { useEffect, useState } from 'react'

import { RefreshCw, Sparkles } from '@/shared/lib/icons'
import { BTN_SUBTLE, HINT_TEXT, SettingCard, SettingRow, ShortcutRecorder } from '@/shared/panel'
import { strings } from '@/shared/strings'

const INITIAL_STATE: DesktopShortcutsState = {
  config: { ...DEFAULT_SHORTCUTS },
  status: {
    openLiving: { registered: false },
    openWorkbench: { registered: false },
    toggleVisibility: { registered: false }
  }
}

export function ShortcutsPage(): React.JSX.Element {
  const t = strings.settings.shortcuts
  const [state, setState] = useState<DesktopShortcutsState>(INITIAL_STATE)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true

    void window.spiritagent.shortcuts
      ?.get()
      .then(res => {
        if (active && res) {
          setState(res)
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })

    const off = window.spiritagent.shortcuts?.onChanged?.(next => {
      if (active && next) {
        setState(next)
      }
    })

    return () => {
      active = false
      off?.()
    }
  }, [])

  const handleChange = async (key: keyof DesktopShortcutsConfig, value: string): Promise<void> => {
    try {
      const res = await window.spiritagent.shortcuts.set({
        shortcuts: { [key]: value }
      })

      if (res) {
        setState(res)
      }
    } catch {
      // 保持当前状态；UI 会在下一次推送时对齐
    }
  }

  const handleResetAll = async (): Promise<void> => {
    try {
      const res = await window.spiritagent.shortcuts.set({
        shortcuts: { ...DEFAULT_SHORTCUTS }
      })

      if (res) {
        setState(res)
      }
    } catch {
      // 保持当前状态
    }
  }

  const isAllDefault =
    state.config.toggleVisibility === DEFAULT_SHORTCUTS.toggleVisibility &&
    state.config.openLiving === DEFAULT_SHORTCUTS.openLiving &&
    state.config.openWorkbench === DEFAULT_SHORTCUTS.openWorkbench

  return (
    <div className="space-y-4">
      <div className="pt-1 pb-1">
        <h2 className="text-sm font-semibold text-strong">{t.heading}</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-faint">{t.intro}</p>
      </div>
      <SettingCard>
        <SettingRow description={t.toggleVisibilityDesc} label={t.toggleVisibility}>
          <ShortcutRecorder
            defaultValue={DEFAULT_SHORTCUTS.toggleVisibility}
            disabled={loading}
            error={state.status.toggleVisibility?.error}
            onChange={val => void handleChange('toggleVisibility', val)}
            registered={state.status.toggleVisibility?.registered}
            value={state.config.toggleVisibility}
          />
        </SettingRow>
        <SettingRow description={t.openLivingDesc} label={t.openLiving}>
          <ShortcutRecorder
            defaultValue={DEFAULT_SHORTCUTS.openLiving}
            disabled={loading}
            error={state.status.openLiving?.error}
            onChange={val => void handleChange('openLiving', val)}
            registered={state.status.openLiving?.registered}
            value={state.config.openLiving}
          />
        </SettingRow>
        <SettingRow description={t.openWorkbenchDesc} label={t.openWorkbench}>
          <ShortcutRecorder
            defaultValue={DEFAULT_SHORTCUTS.openWorkbench}
            disabled={loading}
            error={state.status.openWorkbench?.error}
            onChange={val => void handleChange('openWorkbench', val)}
            registered={state.status.openWorkbench?.registered}
            value={state.config.openWorkbench}
          />
        </SettingRow>
      </SettingCard>

      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-1">
        <div className="flex items-center gap-1.5 text-faint">
          <Sparkles className="size-3.5 shrink-0 text-accent" />
          <p className={HINT_TEXT}>{t.pressKeysHint}</p>
        </div>

        {!isAllDefault && (
          <button className={BTN_SUBTLE} disabled={loading} onClick={() => void handleResetAll()} type="button">
            <RefreshCw className="size-3.5" />
            <span>{t.resetAll}</span>
          </button>
        )}
      </div>
    </div>
  )
}
