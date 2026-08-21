import { SegmentedControl } from '@/shared/components/ui'
import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { strings } from '@/shared/strings'

import { SettingsContent } from './primitives'
import { SkillsSettings } from './skills-settings'
import { ToolsetsSettings } from './toolsets-settings'

const SUBTABS = ['skills', 'toolsets'] as const
type SkillsToolsSubtab = (typeof SUBTABS)[number]

export function SkillsToolsTabs(): React.JSX.Element {
  const t = strings
  const [subtab, setSubtab] = useRouteEnumParam<SkillsToolsSubtab>('subtab', SUBTABS, 'skills')

  return (
    <SettingsContent>
      <div className="mb-4">
        <SegmentedControl
          onChange={setSubtab}
          options={[
            { id: 'skills', label: t.skills.tabSkills },
            { id: 'toolsets', label: t.skills.tabToolsets }
          ]}
          value={subtab}
        />
      </div>
      {subtab === 'skills' ? <SkillsSettings /> : <ToolsetsSettings />}
    </SettingsContent>
  )
}
