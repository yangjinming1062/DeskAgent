// 工作台工位设置抽屉：仅 inference / runner / skills / toolsets 四类工作向表单；
// 复用 setting/settings/* 组件，只换容器。

import { useStore } from '@nanostores/react'
import type React from 'react'

import { $appSettingsView, type AppSettingsView } from '@/setting/app-settings/app-settings-view'
import { InferenceSettings } from '@/setting/settings/inference-settings'
import { RunnerSettings } from '@/setting/settings/runner-settings'
import { SkillsToolsTabs } from '@/setting/settings/skills-tools-tabs'
import { Brain, Cpu, Sparkles } from '@/shared/lib/icons'
import { type NavItemDescriptor, SettingsNav } from '@/shared/panel'

export type StationSettingsTab = 'inference' | 'runner' | 'skills'

// 工作台三类工作设置入口；其它生活向设置（外观 / 快捷键 / 语音 / 通道 / 关于 / 账号）由生活空间托管。
const STATION_TABS: NavItemDescriptor[] = [
  { id: 'inference', icon: Brain, label: '推理与对话' },
  { id: 'runner', icon: Cpu, label: '本机执行器' },
  { id: 'skills', icon: Sparkles, label: '技能与工具' }
]

export function StationSettings(): React.JSX.Element {
  const view = useStore($appSettingsView)

  const activeTab: StationSettingsTab = view === 'runner' || view === 'skills' ? view : 'inference'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b border-line-standard px-3 py-2">
        <SettingsNav
          activeId={activeTab}
          items={STATION_TABS}
          onSelect={id => $appSettingsView.set(id as AppSettingsView)}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {activeTab === 'inference' ? <InferenceSettings /> : null}
        {activeTab === 'runner' ? <RunnerSettings /> : null}
        {activeTab === 'skills' ? <SkillsToolsTabs /> : null}
      </div>
    </div>
  )
}
