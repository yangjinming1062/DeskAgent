import { SegmentedControl } from '@/shared/components/ui/segmented-control'
import { useI18n } from '@/shared/i18n'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'

import { SettingsContent } from './primitives'
import { SkillsSettings } from './skills-settings'
import { ToolsetsSettings } from './toolsets-settings'

const SUBTABS = ['skills', 'toolsets'] as const
type SkillsToolsSubtab = (typeof SUBTABS)[number]

export function SkillsToolsTabs() {
  const { t } = useI18n()
  const [subtab, setSubtab] = useRouteEnumParam('subtab', SUBTABS, 'skills')

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
