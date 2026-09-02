import { IconPalette } from '@tabler/icons-react'
import { IconBrandWechat } from '@tabler/icons-react'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { AudioLines, Brain, Cpu, Info, Keyboard, Settings, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { type NavItemDescriptor, SettingsNav, SURFACE_CHROME } from '@/shared/panel'
import { strings } from '@/shared/strings'

import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AppearanceSettings } from './appearance-settings'
import { ChannelsSettings } from './channels-settings'
import { InferenceSettings } from './inference-settings'
import { RunnerSettings } from './runner-settings'
import { ShortcutsSettings } from './shortcuts-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import { SpeechSettings } from './speech-settings'
import type { SettingsPageProps, SettingsTab } from './types'

const SETTINGS_VIEWS = [
  'inference',
  'speech',
  'channels',
  'appearance',
  'shortcuts',
  'runner',
  'skills',
  'about'
] as const satisfies readonly SettingsTab[]

const TAB_COMPONENTS: Record<SettingsTab, React.ComponentType> = {
  about: AboutSettings,
  appearance: AppearanceSettings,
  channels: ChannelsSettings,
  inference: InferenceSettings,
  runner: RunnerSettings,
  shortcuts: ShortcutsSettings,
  skills: SkillsToolsTabs,
  speech: SpeechSettings
}

// 配置云端真源 + 自动同步（PROTOCOL §2.4）后，导入/导出/恢复默认三按钮按设计移除：
// 修改即时上云、跨端经水合收敛，文件搬运与本地重置入口不再需要。
export function SettingsView({ onClose, onConfigSaved }: SettingsPageProps): React.JSX.Element {
  const t = strings
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'inference')

  const navItems: NavItemDescriptor[] = [
    { id: 'inference', label: t.settings.nav.inference, icon: Brain },
    { id: 'speech', label: t.speech.title, icon: AudioLines },
    { id: 'channels', label: t.settings.nav.channels, icon: IconBrandWechat },
    { id: 'appearance', label: t.settings.nav.appearance, icon: IconPalette },
    { id: 'shortcuts', label: t.settings.nav.shortcuts, icon: Keyboard },
    { id: 'runner', label: t.settings.nav.runner, icon: Cpu },
    { id: 'skills', label: t.settings.nav.skills, icon: Sparkles },
    { id: 'about', label: t.settings.nav.about, icon: Info }
  ]

  const Panel = TAB_COMPONENTS[activeView] ?? InferenceSettings

  return (
    <OverlayView closeLabel={t.settings.closeSettings} icon={Settings} onClose={onClose} title={t.settings.title}>
      <div className="flex min-h-0 flex-1">
        <aside className={cn(SURFACE_CHROME, 'flex w-52 shrink-0 flex-col border-r border-line-standard p-2.5')}>
          <SettingsNav activeId={activeView} items={navItems} onSelect={id => setActiveView(id as SettingsTab)} />
        </aside>
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {activeView === 'inference' ? <InferenceSettings onConfigSaved={onConfigSaved} /> : <Panel />}
        </main>
      </div>
    </OverlayView>
  )
}
