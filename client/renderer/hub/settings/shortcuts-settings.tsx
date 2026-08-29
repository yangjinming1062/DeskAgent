import { DEFAULT_SHORTCUTS, type DesktopShortcutsConfig, type DesktopShortcutsState } from '@ipc/contracts'
import type React from 'react'
import { useEffect, useState } from 'react'

import { RefreshCw, Sparkles } from '@/shared/lib/icons'
import { BTN_SUBTLE, HINT_TEXT, SettingCard, SettingRow, SettingsPage, ShortcutRecorder } from '@/shared/panel'
import { strings } from '@/shared/strings'

const INITIAL_STATE: DesktopShortcutsState = {
  config: { ...DEFAULT_SHORTCUTS },
  status: {
    toggleChat: { registered: true },
    toggleVisibility: { registered: true }
  }
}

export function ShortcutsSettings(): React.JSX.Element {
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
      // 异常已由主进程捕获并通过 status 返回
    }
  }

  const handleResetAll = async (): Promise<void> => {
    try {
      const res = await window.spiritagent.shortcuts.reset()

      if (res) {
        setState(res)
      }
    } catch {
      // 异常已由主进程捕获
    }
  }

  const isAllDefault =
    state.config.toggleVisibility === DEFAULT_SHORTCUTS.toggleVisibility &&
    state.config.toggleChat === DEFAULT_SHORTCUTS.toggleChat

  return (
    <SettingsPage hint={t.intro} title={t.heading}>
      <div className="space-y-6">
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
          <SettingRow description={t.toggleChatDesc} label={t.toggleChat}>
            <ShortcutRecorder
              defaultValue={DEFAULT_SHORTCUTS.toggleChat}
              disabled={loading}
              error={state.status.toggleChat?.error}
              onChange={val => void handleChange('toggleChat', val)}
              registered={state.status.toggleChat?.registered}
              value={state.config.toggleChat}
            />
          </SettingRow>
        </SettingCard>

        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-1">
          <div className="flex items-center gap-1.5 text-white/40">
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
    </SettingsPage>
  )
}
