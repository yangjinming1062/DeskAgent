import { useCallback, useMemo, useState } from 'react'
import type React from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  EmptyState,
  INPUT_CLASS,
  ListRow,
  LoadingBlock,
  PanelSelect,
  SectionHeading,
  SettingsSubsection,
  Toggle
} from '@/shared/panel'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { getIn, setIn, useRunnerConfig } from './use-runner-config'

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

function readInputValue(row: InputRow, raw: string): string | number {
  if (row.type !== 'number') {
    return raw
  }

  // Number('') 是 0 而不是 NaN——把空输入视为无效，回退到默认值，
  // 而不是悄悄写入 0。
  if (raw === '') {
    return row.default
  }

  const parsed = Number(raw)

  return Number.isNaN(parsed) ? row.default : parsed
}

export function RunnerPage(): React.JSX.Element {
  const r = strings.settings.runner

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

  const updateField = useCallback(
    (path: readonly string[], value: unknown) => {
      // 调用点已被 `if (!config)` 早返守卫过，这里 config 一定非空。
      setConfig(prev => (prev ? setIn(prev, path, value) : prev))
      setIsDirty(true)
    },
    [setConfig]
  )

  const envType = ((getIn(config, ['terminal', 'env_type']) as string) || 'local').toLowerCase()

  // SSH 连接参数只在环境类型选了 SSH 时出现，其余分组行是稳定的模块级结构
  const rowGroups = useMemo<readonly { heading: string; rows: readonly Row[] }[]>(() => {
    const groups: { heading: string; rows: readonly Row[] }[] = [
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
      }
    ]

    if (envType === 'ssh') {
      groups.push({
        heading: r.ssh,
        rows: [
          { kind: 'input', path: ['terminal', 'ssh', 'host'], title: r.sshHost, default: '' },
          { kind: 'input', type: 'number', path: ['terminal', 'ssh', 'port'], title: r.sshPort, default: 22 },
          { kind: 'input', path: ['terminal', 'ssh', 'user'], title: r.sshUser, default: '' },
          { kind: 'input', type: 'password', path: ['terminal', 'ssh', 'password'], title: r.sshPassword, default: '' },
          { kind: 'input', path: ['terminal', 'ssh', 'key'], title: r.sshKey, default: '' }
        ]
      })
    }

    groups.push(
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
    )

    return groups
  }, [envType, r])

  if (isLoading) {
    return <LoadingBlock label={r.loading} />
  }

  if (!config) {
    return <EmptyState title={r.failedLoad} />
  }

  return (
    <SettingsSubsection intro={r.intro} title={r.title}>
      <div className="space-y-4">
        {rowGroups.map(group => (
          <div className="space-y-2" key={group.heading}>
            <SectionHeading title={group.heading} />
            <div className="space-y-1.5">
              {group.rows.map(row => (
                <ListRow action={renderRowAction(row, config, updateField)} key={row.title} title={row.title} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-8 flex justify-end">
        <button className={BTN_PRIMARY} disabled={isSaving || !isDirty} onClick={() => void handleSave()} type="button">
          {isSaving ? strings.common.saving : r.save}
        </button>
      </div>
    </SettingsSubsection>
  )
}

function renderRowAction(
  row: Row,
  config: Record<string, unknown>,
  updateField: (path: readonly string[], value: unknown) => void
): React.ReactNode {
  if (row.kind === 'select') {
    return (
      <PanelSelect
        onChange={v => updateField(row.path, v)}
        options={row.options}
        value={(getIn(config, row.path) as string) || row.default}
      />
    )
  }

  if (row.kind === 'switch') {
    return <Toggle checked={!!getIn(config, row.path)} onChange={v => updateField(row.path, v)} />
  }

  // row.kind === 'input'
  return (
    <input
      className={cn(INPUT_CLASS, 'w-36')}
      onChange={e => updateField(row.path, readInputValue(row, e.target.value))}
      type={row.type || 'text'}
      value={(getIn(config, row.path) as string | number) ?? row.default}
    />
  )
}
