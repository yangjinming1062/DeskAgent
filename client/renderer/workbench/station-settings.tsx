// 工作台工位设置抽屉：仅 inference / runner / skills 三类工作向表单。
//
// 模块内 tab 状态，不读全局 appSettingsView；
// 初始 tab 从 `#/inference|runner|skills` hash 解析，
// 抽屉打开期间再收到 hashchange 也会同步切到对应 tab。

import type React from 'react'
import { useEffect, useState } from 'react'

import { Brain, Cpu, Sparkles } from '@/shared/lib/icons'
import { type NavItemDescriptor, SettingsContent, SettingsNav } from '@/shared/panel'

import { InferencePage } from './station/inference-page'
import { RunnerPage } from './station/runner-page'
import { SkillsToolsTabs } from './station/skills-tools-tabs'

export type StationSettingsTab = 'inference' | 'runner' | 'skills'

// 工作台三类工作设置入口；其它生活向设置（外观 / 快捷键 / 语音 / 通道 / 关于 / 账号）由生活空间托管。
const STATION_NAV: NavItemDescriptor[] = [
  { id: 'inference', icon: Brain, label: '推理与对话' },
  { id: 'runner', icon: Cpu, label: '本机执行器' },
  { id: 'skills', icon: Sparkles, label: '技能与工具' }
]

const STATION_TAB_IDS: ReadonlyArray<StationSettingsTab> = STATION_NAV.map(n => n.id as StationSettingsTab)

function readHashTab(allowed: ReadonlyArray<StationSettingsTab>): StationSettingsTab {
  if (typeof window === 'undefined' || !window.location.hash) {
    return 'inference'
  }

  const raw = window.location.hash.replace(/^#\/?/, '').trim().toLowerCase()
  const pathOnly = raw.split('?')[0]

  const segment = pathOnly.startsWith('settings/')
    ? pathOnly.slice('settings/'.length)
    : pathOnly.startsWith('station/')
      ? pathOnly.slice('station/'.length)
      : pathOnly

  return (allowed as ReadonlyArray<string>).includes(segment) ? (segment as StationSettingsTab) : 'inference'
}

export function StationSettings(): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<StationSettingsTab>(() => readHashTab(STATION_TAB_IDS))

  // 工作台窗口内 hash 变化时同步切 tab（深链入站 + 抽屉打开期间被再次定位）。
  useEffect(() => {
    const onHash = (): void => setActiveTab(readHashTab(STATION_TAB_IDS))

    window.addEventListener('hashchange', onHash)

    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleSelectTab = (id: string): void => {
    const next = id as StationSettingsTab
    setActiveTab(next)

    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', `#/${next}`)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b border-line-standard px-3 py-2">
        <SettingsNav activeId={activeTab} items={STATION_NAV} onSelect={handleSelectTab} />
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        <SettingsContent>
          {activeTab === 'inference' ? <InferencePage /> : null}
          {activeTab === 'runner' ? <RunnerPage /> : null}
          {activeTab === 'skills' ? <SkillsToolsTabs /> : null}
        </SettingsContent>
      </div>
    </div>
  )
}
