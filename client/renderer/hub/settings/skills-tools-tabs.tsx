import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { strings } from '@/shared/strings'

import { SettingsContent } from './primitives'
import { SkillsSettings } from './skills-settings'
import { ToolsetsSettings } from './toolsets-settings'

const SUBTABS = ['skills', 'toolsets'] as const
type SkillsToolsSubtab = (typeof SUBTABS)[number]

// 子 tab 走伙伴设置风格的横向圆角按钮（bg-white/15 active / bg-white/5 inactive），
// 不再借用 SegmentedControl 的浅色 chrome。
function SubtabPill({
  active,
  children,
  onClick
}: {
  active: boolean
  children: React.ReactNode
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      className={`flex-1 rounded-lg border px-3 py-1.5 text-xs transition ${
        active
          ? 'border-white/60 bg-white/15 font-medium text-white'
          : 'border-white/15 bg-white/5 text-white/70 hover:bg-white/10'
      }`}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  )
}

export function SkillsToolsTabs(): React.JSX.Element {
  const t = strings
  const [subtab, setSubtab] = useRouteEnumParam<SkillsToolsSubtab>('subtab', SUBTABS, 'skills')

  return (
    <SettingsContent>
      <div className="mb-4 flex w-44 gap-1.5">
        <SubtabPill active={subtab === 'skills'} onClick={() => setSubtab('skills')}>
          {t.skills.tabSkills}
        </SubtabPill>
        <SubtabPill active={subtab === 'toolsets'} onClick={() => setSubtab('toolsets')}>
          {t.skills.tabToolsets}
        </SubtabPill>
      </div>
      {subtab === 'skills' ? <SkillsSettings /> : <ToolsetsSettings />}
    </SettingsContent>
  )
}
