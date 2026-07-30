import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/shared/i18n'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Settings } from '@/shared/lib/icons'
import { notify, notifyError } from '@/shared/store/notifications'

import { EmptyState, ListRow, LoadingState, SectionHeading, SettingsContent, SettingsSubsection } from './primitives'
import { useRunnerConfig } from './use-runner-config'

type SelectRow = {
  kind: 'select'
  path: readonly string[]
  title: string
  options: readonly { value: string; label: string }[]
  default: string
}

type SwitchRow = {
  kind: 'switch'
  path: readonly string[]
  title: string
}

type InputRow = {
  kind: 'input'
  type?: 'text' | 'number'
  path: readonly string[]
  title: string
  default: string | number
}

type Row = SelectRow | SwitchRow | InputRow

const BACKEND_OPTIONS = [
  { value: 'local', label: 'Local' },
  { value: 'docker', label: 'Docker' },
  { value: 'ssh', label: 'SSH' },
  { value: 'singularity', label: 'Singularity' }
]

const BROWSER_ENGINE_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'lightpanda', label: 'Lightpanda' },
  { value: 'chrome', label: 'Chrome' }
]

export function RunnerSettings() {
  const { t } = useI18n()
  const r = t.settings.runner

  const { yamlDoc, setYamlDoc, isLoading, write } = useRunnerConfig(r.failedLoad)
  const [isSaving, setIsSaving] = useState(false)
  const [isDirty, setIsDirty] = useState(false)

  const handleSave = async () => {
    if (!yamlDoc) {
      return
    }

    setIsSaving(true)

    try {
      const result = await write(yamlDoc.toString())

      if (!result.ok) {
        throw new Error(result.error)
      }

      triggerHaptic('success')

      if (!result.restarted && result.restartError) {
        notify({ kind: 'warning', message: r.saveRestartFailed(result.restartError) })
      } else {
        notify({ kind: 'success', message: r.saveSuccess })
      }

      setIsDirty(false)
    } catch (err) {
      notifyError(err, r.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

  const updateField = (path: readonly string[], value: unknown) => {
    if (!yamlDoc) {
      return
    }

    const newDoc = yamlDoc.clone()
    newDoc.setIn(path as Iterable<unknown>, value)
    setYamlDoc(newDoc)
    setIsDirty(true)
  }

  // Locale is locked in this app (I18nProvider hardcodes zh), so the row
  // config is a stable module-level shape — no useMemo needed.
  const rowGroups: readonly { heading: string; rows: readonly Row[] }[] = [
    {
      heading: r.terminal,
      rows: [
        {
          kind: 'select',
          path: ['terminal', 'env_type'],
          title: r.terminalEnvType,
          options: BACKEND_OPTIONS,
          default: 'local'
        }
      ]
    },
    {
      heading: r.browser,
      rows: [
        {
          kind: 'select',
          path: ['browser', 'engine'],
          title: r.browserEngine,
          options: BROWSER_ENGINE_OPTIONS,
          default: 'auto'
        },
        { kind: 'switch', path: ['browser', 'allow_private_urls'], title: r.browserAllowPrivateUrls },
        { kind: 'switch', path: ['browser', 'record_sessions'], title: r.browserRecordSessions }
      ]
    },
    {
      heading: r.security,
      rows: [{ kind: 'switch', path: ['security', 'redact_secrets'], title: r.securityRedactSecrets }]
    },
    {
      heading: r.auxiliary,
      rows: [
        {
          kind: 'input',
          type: 'number',
          path: ['auxiliary', 'vision', 'timeout'],
          title: r.auxiliaryVisionTimeout,
          default: 120
        },
        {
          kind: 'input',
          type: 'number',
          path: ['auxiliary', 'vision', 'temperature'],
          title: r.auxiliaryVisionTemperature,
          default: 0.1
        }
      ]
    },
    {
      heading: r.debug,
      rows: [
        { kind: 'switch', path: ['debug', 'interrupt'], title: r.debugInterrupt },
        { kind: 'switch', path: ['debug', 'vision_tools'], title: r.debugVisionTools }
      ]
    }
  ]

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingState label={r.loading} />
      </SettingsContent>
    )
  }

  if (!yamlDoc) {
    return (
      <SettingsContent>
        <EmptyState description={r.failedLoad} title={r.failedLoad} />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <SettingsSubsection icon={Settings} intro={r.intro} title={r.title}>
        <div className="space-y-4">
          {rowGroups.map(group => (
            <div key={group.heading}>
              <SectionHeading icon={Settings} title={group.heading} />
              <div className="divide-y divide-(--ui-stroke-tertiary)">
                {group.rows.map(row => (
                  <ListRow
                    action={
                      row.kind === 'select' ? (
                        <Select
                          onValueChange={v => updateField(row.path, v)}
                          value={(yamlDoc.getIn(row.path as Iterable<unknown>, false) as string) || row.default}
                        >
                          <SelectTrigger className="w-36">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {row.options.map(opt => (
                              <SelectItem key={opt.value} value={opt.value}>
                                {opt.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : row.kind === 'input' ? (
                        <Input
                          className="w-36"
                          onChange={e => {
                            const val = e.target.value

                            // Number('') is 0, not NaN — treat empty input as
                            // invalid so clearing a number field falls back to
                            // the default instead of silently saving 0.
                            if (row.type === 'number') {
                              const parsed = val === '' ? NaN : Number(val)
                              updateField(row.path, isNaN(parsed) ? row.default : parsed)
                            } else {
                              updateField(row.path, val)
                            }
                          }}
                          type={row.type || 'text'}
                          value={(yamlDoc.getIn(row.path as Iterable<unknown>) as string | number) ?? row.default}
                        />
                      ) : (
                        <Switch
                          checked={!!yamlDoc.getIn(row.path as Iterable<unknown>, false)}
                          onCheckedChange={v => updateField(row.path, v)}
                        />
                      )
                    }
                    key={row.title}
                    title={row.title}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-8 flex justify-end">
          <Button disabled={isSaving || !isDirty} onClick={() => void handleSave()}>
            {isSaving ? t.common.saving : r.save}
          </Button>
        </div>
      </SettingsSubsection>
    </SettingsContent>
  )
}
