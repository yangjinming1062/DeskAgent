import { IconPalette } from '@tabler/icons-react'
import { IconBrandWechat } from '@tabler/icons-react'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { AudioLines, Cpu, Info, KeyRound, Settings, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { type NavItemDescriptor, SettingsNav, SURFACE_CHROME } from '@/shared/panel'
import { strings } from '@/shared/strings'

import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { AppearanceSettings } from './appearance-settings'
import { ChannelsSettings } from './channels-settings'
import { RunnerSettings } from './runner-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import { SpeechSettings } from './speech-settings'
import type { SettingsPageProps, SettingsView as SettingsViewId } from './types'

const SETTINGS_VIEWS = [
  'account',
  'speech',
  'channels',
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

  const navItems: NavItemDescriptor[] = [
    { id: 'account', label: t.settings.nav.account, icon: KeyRound },
    { id: 'speech', label: t.speech.title, icon: AudioLines },
    { id: 'channels', label: t.settings.nav.channels, icon: IconBrandWechat },
    { id: 'appearance', label: t.settings.nav.appearance, icon: IconPalette },
    { id: 'runner', label: t.settings.nav.runner, icon: Cpu },
    { id: 'skills', label: t.settings.nav.skills, icon: Sparkles },
    { id: 'about', label: t.settings.nav.about, icon: Info }
  ]

  return (
    <OverlayView closeLabel={t.settings.closeSettings} icon={Settings} onClose={onClose} title={t.settings.title}>
      <div className="flex min-h-0 flex-1">
        <aside className={cn(SURFACE_CHROME, 'flex w-52 shrink-0 flex-col border-r border-white/10 p-2.5')}>
          <SettingsNav activeId={activeView} items={navItems} onSelect={id => setActiveView(id as SettingsViewId)} />
        </aside>
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {activeView === 'account' ? (
            <AccountSettings onConfigSaved={onConfigSaved} />
          ) : activeView === 'speech' ? (
            <SpeechSettings />
          ) : activeView === 'channels' ? (
            <ChannelsSettings />
          ) : activeView === 'appearance' ? (
            <AppearanceSettings />
          ) : activeView === 'runner' ? (
            <RunnerSettings />
          ) : activeView === 'skills' ? (
            <SkillsToolsTabs />
          ) : (
            <AboutSettings />
          )}
        </main>
      </div>
    </OverlayView>
  )
}
