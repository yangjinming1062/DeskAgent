import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { Segmented } from '@/shared/panel'
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
      <div className="mb-4 w-44">
        <Segmented<SkillsToolsSubtab>
          onChange={setSubtab}
          options={[
            { value: 'skills', label: t.skills.tabSkills },
            { value: 'toolsets', label: t.skills.tabToolsets }
          ]}
          value={subtab}
        />
      </div>
      {subtab === 'skills' ? <SkillsSettings /> : <ToolsetsSettings />}
    </SettingsContent>
  )
}
