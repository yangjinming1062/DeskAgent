import { IconDownload, IconRefresh, IconUpload } from '@tabler/icons-react'
import { useRef } from 'react'

import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { Archive, Info, KeyRound, Palette, Settings, Sparkles, Wrench } from '@/lib/icons'
import { notifyError } from '@/store/notifications'
import { getZastConfigDefaults, getZastConfigRecord, saveZastConfig } from '@/zast'

import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { OverlayIconButton } from '../overlays/overlay-chrome'
import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { AppearanceSettings } from './appearance-settings'
import { McpSettings } from './mcp-settings'
import { RunnerSettings } from './runner-settings'
import { SessionsSettings } from './sessions-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import type { SettingsPageProps } from './types'

type SettingsViewId = 'about' | 'account' | 'appearance' | 'mcp' | 'runner' | 'sessions' | 'skills'

const SETTINGS_VIEWS: readonly SettingsViewId[] = [
  'appearance',
  'account',
  'runner',
  'skills',
  'mcp',
  'sessions',
  'about'
]

export function SettingsView({ gateway, onClose, onConfigSaved }: SettingsPageProps) {
  const { t } = useI18n()
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'appearance')

  const importInputRef = useRef<HTMLInputElement | null>(null)

  const exportConfig = async () => {
    try {
      const cfg = await getZastConfigRecord()
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'zast-config.json'
      a.click()
      URL.revokeObjectURL(url)
      triggerHaptic('success')
    } catch (err) {
      notifyError(err, t.settings.exportFailed)
    }
  }

  const resetConfig = async () => {
    if (!window.confirm(t.settings.resetConfirm)) {
      return
    }

    try {
      await saveZastConfig(await getZastConfigDefaults())
      triggerHaptic('success')
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, t.settings.resetFailed)
    }
  }

  return (
    <OverlayView closeLabel={t.settings.closeSettings} onClose={onClose}>
      <OverlaySplitLayout>
        <OverlaySidebar>
          <OverlayNavItem
            active={activeView === 'appearance'}
            icon={Palette}
            label={t.settings.nav.appearance ?? 'Appearance'}
            onClick={() => setActiveView('appearance')}
          />
          <OverlayNavItem
            active={activeView === 'account'}
            icon={KeyRound}
            label={t.settings.nav.account}
            onClick={() => setActiveView('account')}
          />
          <OverlayNavItem
            active={activeView === 'runner'}
            icon={Settings}
            label={t.settings.nav.runner ?? 'Runner'}
            onClick={() => setActiveView('runner')}
          />
          <OverlayNavItem
            active={activeView === 'skills'}
            icon={Sparkles}
            label={t.settings.nav.skills ?? 'Skills'}
            onClick={() => setActiveView('skills')}
          />
          <div className="my-2 h-px bg-border/30" />
          <OverlayNavItem
            active={activeView === 'mcp'}
            icon={Wrench}
            label={t.settings.nav.mcp}
            onClick={() => setActiveView('mcp')}
          />
          <OverlayNavItem
            active={activeView === 'sessions'}
            icon={Archive}
            label={t.settings.nav.archivedChats}
            onClick={() => setActiveView('sessions')}
          />
          <div className="my-2 h-px bg-border/30" />
          <OverlayNavItem
            active={activeView === 'about'}
            icon={Info}
            label={t.settings.nav.about}
            onClick={() => setActiveView('about')}
          />
          <div className="mt-auto flex items-center gap-1 pt-2">
            <Tip label={t.settings.exportConfig}>
              <OverlayIconButton onClick={() => void exportConfig()}>
                <IconDownload className="size-3.5" />
              </OverlayIconButton>
            </Tip>
            <Tip label={t.settings.importConfig}>
              <OverlayIconButton
                onClick={() => {
                  triggerHaptic('open')
                  importInputRef.current?.click()
                }}
              >
                <IconUpload className="size-3.5" />
              </OverlayIconButton>
            </Tip>
            <Tip label={t.settings.resetToDefaults}>
              <OverlayIconButton
                className="hover:text-destructive"
                onClick={() => {
                  triggerHaptic('warning')
                  void resetConfig()
                }}
              >
                <IconRefresh className="size-3.5" />
              </OverlayIconButton>
            </Tip>
          </div>
        </OverlaySidebar>

        <OverlayMain className="px-0 pb-0 pt-[calc(var(--titlebar-height)+1rem)]">
          {activeView === 'appearance' ? (
            <AppearanceSettings />
          ) : activeView === 'account' ? (
            <AccountSettings onConfigSaved={onConfigSaved} />
          ) : activeView === 'runner' ? (
            <RunnerSettings />
          ) : activeView === 'skills' ? (
            <SkillsToolsTabs />
          ) : activeView === 'about' ? (
            <AboutSettings />
          ) : activeView === 'mcp' ? (
            <McpSettings gateway={gateway} onConfigSaved={onConfigSaved} />
          ) : (
            <SessionsSettings />
          )}
        </OverlayMain>
      </OverlaySplitLayout>
    </OverlayView>
  )
}

export { SettingsView as SettingsPage }
