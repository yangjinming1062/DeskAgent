import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { Segmented } from '@/shared/panel'
import { strings } from '@/shared/strings'

import { SkillsPage } from './skills-page'
import { ToolsetsPage } from './toolsets-page'

const SUBTABS = ['skills', 'toolsets'] as const
type SkillsToolsSubtab = (typeof SUBTABS)[number]

export function SkillsToolsTabs(): React.JSX.Element {
  const t = strings
  const [subtab, setSubtab] = useRouteEnumParam<SkillsToolsSubtab>('subtab', SUBTABS, 'skills')

  return (
    <>
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
      {subtab === 'skills' ? <SkillsPage /> : <ToolsetsPage />}
    </>
  )
}
