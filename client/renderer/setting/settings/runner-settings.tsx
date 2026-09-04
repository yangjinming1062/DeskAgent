import { useState } from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, INPUT_CLASS, LoadingBlock, PanelSelect, Toggle } from '@/shared/panel'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { getIn, setIn, useRunnerConfig } from '../runner/use-runner-config'

import { EmptyState, ListRow, SectionHeading, SettingsContent, SettingsSubsection } from './primitives'

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
  type?: 'text' | 'number' | 'password'
  path: readonly string[]
  title: string
  default: string | number
}

type Row = SelectRow | SwitchRow | InputRow

const BACKEND_OPTIONS = [
  { value: 'local', label: 'Local' },
  { value: 'ssh', label: 'SSH' }
]

export function RunnerSettings(): React.JSX.Element {
  const t = strings
  const r = t.settings.runner

  const { config, setConfig, isLoading, write } = useRunnerConfig(r.failedLoad)
  const [isSaving, setIsSaving] = useState(false)
  const [isDirty, setIsDirty] = useState(false)

  const handleSave = async () => {
    if (!config) {
      return
    }

    setIsSaving(true)

    try {
      const result = await write(JSON.stringify(config, null, 2))

      if (!result.ok) {
        throw new Error(result.error)
      }

      triggerHaptic('success')
      notify({ kind: 'success', message: r.saveSuccess })

      setIsDirty(false)
    } catch (err) {
      notifyError(err, r.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

  const updateField = (path: readonly string[], value: unknown) => {
    if (!config) {
      return
    }

    setConfig(setIn(config, path, value))
    setIsDirty(true)
  }

  const envType = ((getIn(config, ['terminal', 'env_type']) as string) || 'local').toLowerCase()

  const sshRows: readonly Row[] = [
    { kind: 'input', path: ['terminal', 'ssh', 'host'], title: r.sshHost, default: '' },
    { kind: 'input', type: 'number', path: ['terminal', 'ssh', 'port'], title: r.sshPort, default: 22 },
    { kind: 'input', path: ['terminal', 'ssh', 'user'], title: r.sshUser, default: '' },
    { kind: 'input', type: 'password', path: ['terminal', 'ssh', 'password'], title: r.sshPassword, default: '' },
    { kind: 'input', path: ['terminal', 'ssh', 'key'], title: r.sshKey, default: '' }
  ]

  // SSH 连接参数只在环境类型选了 SSH 时出现，其余分组行是稳定的模块级结构
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
    ...(envType === 'ssh' ? [{ heading: r.ssh, rows: sshRows }] : []),
    {
      heading: r.browser,
      rows: [{ kind: 'switch', path: ['browser', 'allow_private_urls'], title: r.browserAllowPrivateUrls }]
    },
    {
      heading: r.security,
      rows: [{ kind: 'switch', path: ['security', 'redact_secrets'], title: r.securityRedactSecrets }]
    },
    {
      heading: r.debug,
      rows: [{ kind: 'switch', path: ['debug', 'interrupt'], title: r.debugInterrupt }]
    }
  ]

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingBlock label={r.loading} />
      </SettingsContent>
    )
  }

  if (!config) {
    return (
      <SettingsContent>
        <EmptyState description={r.failedLoad} title={r.failedLoad} />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <SettingsSubsection intro={r.intro} title={r.title}>
        <div className="space-y-4">
          {rowGroups.map(group => (
            <div className="space-y-2" key={group.heading}>
              <SectionHeading title={group.heading} />
              <div className="space-y-1.5">
                {group.rows.map(row => (
                  <ListRow
                    action={
                      row.kind === 'select' ? (
                        <PanelSelect
                          onChange={v => updateField(row.path, v)}
                          options={row.options}
                          value={(getIn(config, row.path) as string) || row.default}
                        />
                      ) : row.kind === 'input' ? (
                        <input
                          className={cn(INPUT_CLASS, 'w-36')}
                          onChange={e => {
                            const val = e.target.value

                            // Number('') 是 0 而不是 NaN——把空输入视为无效，
                            // 让清空数字字段时回退到默认值，
                            // 而不是悄悄写入 0。
                            if (row.type === 'number') {
                              const parsed = val === '' ? NaN : Number(val)
                              updateField(row.path, isNaN(parsed) ? row.default : parsed)
                            } else {
                              updateField(row.path, val)
                            }
                          }}
                          type={row.type || 'text'}
                          value={(getIn(config, row.path) as string | number) ?? row.default}
                        />
                      ) : (
                        <Toggle checked={!!getIn(config, row.path)} onChange={v => updateField(row.path, v)} />
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
          <button
            className={BTN_PRIMARY}
            disabled={isSaving || !isDirty}
            onClick={() => void handleSave()}
            type="button"
          >
            {isSaving ? t.common.saving : r.save}
          </button>
        </div>
      </SettingsSubsection>
    </SettingsContent>
  )
}
