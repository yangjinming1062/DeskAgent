import { IconDownload, IconRefresh, IconUpload } from '@tabler/icons-react'
import { useRef } from 'react'

import { Tip } from '@/shared/components/ui/tooltip'
import { getDeskAgentConfigDefaults, getDeskAgentConfigRecord, saveDeskAgentConfig } from '@/shared/deskagent'
import { useI18n } from '@/shared/i18n'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Info, KeyRound, Palette, Settings, Sparkles, Wrench } from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { OverlayIconButton } from '../overlays/overlay-chrome'
import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { AppearanceSettings } from './appearance-settings'
import { McpSettings } from './mcp-settings'
import { RunnerSettings } from './runner-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import type { SettingsPageProps } from './types'

type SettingsViewId = 'about' | 'account' | 'appearance' | 'mcp' | 'runner' | 'skills'

const SETTINGS_VIEWS: readonly SettingsViewId[] = ['appearance', 'account', 'runner', 'skills', 'mcp', 'about']

export function SettingsView({ gateway, onClose, onConfigSaved }: SettingsPageProps) {
  const { t } = useI18n()
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'appearance')

  const importInputRef = useRef<HTMLInputElement | null>(null)

  const exportConfig = async () => {
    try {
      const cfg = await getDeskAgentConfigRecord()
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'deskagent-config.json'
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
      await saveDeskAgentConfig(await getDeskAgentConfigDefaults())
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
          ) : activeView === 'mcp' ? (
            <McpSettings gateway={gateway} onConfigSaved={onConfigSaved} />
          ) : (
            <AboutSettings />
          )}
        </OverlayMain>
      </OverlaySplitLayout>
    </OverlayView>
  )
}

export { SettingsView as SettingsPage }
