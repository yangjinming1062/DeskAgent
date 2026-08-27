import { IconPalette } from '@tabler/icons-react'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { AudioLines, Cpu, Info, KeyRound, Sparkles } from '@/shared/lib/icons'
import { strings } from '@/shared/strings'

import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { AppearanceSettings } from './appearance-settings'
import { RunnerSettings } from './runner-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import { SpeechSettings } from './speech-settings'
import type { SettingsPageProps, SettingsView as SettingsViewId } from './types'

const SETTINGS_VIEWS = [
  'account',
  'speech',
  'appearance',
  'runner',
  'skills',
  'about'
] as const satisfies readonly SettingsViewId[]

// 配置云端真源 + 自动同步（PROTOCOL §2.4）后，导入/导出/恢复默认三按钮按设计移除：
// 修改即时上云、跨端经水合收敛，文件搬运与本地重置入口不再需要。
export function SettingsView({ onClose, onConfigSaved }: SettingsPageProps): React.JSX.Element {
  const t = strings
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'account')

  return (
    <OverlayView closeLabel={t.settings.closeSettings} onClose={onClose}>
      <OverlaySplitLayout>
        <OverlaySidebar>
          <OverlayNavItem
            active={activeView === 'account'}
            icon={KeyRound}
            label={t.settings.nav.account}
            onClick={() => setActiveView('account')}
          />
          <OverlayNavItem
            active={activeView === 'speech'}
            icon={AudioLines}
            label={t.speech.title}
            onClick={() => setActiveView('speech')}
          />
          <OverlayNavItem
            active={activeView === 'appearance'}
            icon={IconPalette}
            label={t.settings.nav.appearance}
            onClick={() => setActiveView('appearance')}
          />
          <OverlayNavItem
            active={activeView === 'runner'}
            icon={Cpu}
            label={t.settings.nav.runner}
            onClick={() => setActiveView('runner')}
          />
          <OverlayNavItem
            active={activeView === 'skills'}
            icon={Sparkles}
            label={t.settings.nav.skills}
            onClick={() => setActiveView('skills')}
          />
          <div className="my-2 h-px bg-white/8" />
          <OverlayNavItem
            active={activeView === 'about'}
            icon={Info}
            label={t.settings.nav.about}
            onClick={() => setActiveView('about')}
          />
        </OverlaySidebar>

        <OverlayMain className="px-0 pb-0 pt-[calc(var(--titlebar-height)+1rem)]">
          {activeView === 'account' ? (
            <AccountSettings onConfigSaved={onConfigSaved} />
          ) : activeView === 'speech' ? (
            <SpeechSettings />
          ) : activeView === 'appearance' ? (
            <AppearanceSettings />
          ) : activeView === 'runner' ? (
            <RunnerSettings />
          ) : activeView === 'skills' ? (
            <SkillsToolsTabs />
          ) : (
            <AboutSettings />
          )}
        </OverlayMain>
      </OverlaySplitLayout>
    </OverlayView>
  )
}
