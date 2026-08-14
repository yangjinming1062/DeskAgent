import { useEffect, useMemo, useState } from 'react'

import { Button, Input, Textarea } from '@/shared/components/ui'
import type { DeskAgentGateway } from '@/shared/deskagent'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { Wrench } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { getIn, setIn, useRunnerConfig } from '../runner/use-runner-config'

import { EmptyState, LoadingState, Pill, SettingsContent } from './primitives'
import { useDeepLinkHighlight } from './use-deep-link-highlight'

interface McpSettingsProps {
  gateway?: DeskAgentGateway | null
  onConfigSaved?: () => void
}

type McpServers = Record<string, Record<string, unknown>>

const EMPTY_SERVER = {
  command: '',
  args: [],
  env: {}
}

function getServers(config: Record<string, unknown> | null): McpServers {
  const raw = config ? getIn(config, ['mcp_servers']) : undefined

  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {}
  }

  return raw as McpServers
}

const transportLabel = (server: Record<string, unknown>) =>
  typeof server.transport === 'string'
    ? server.transport
    : typeof server.url === 'string'
      ? 'http'
      : typeof server.command === 'string'
        ? 'stdio'
        : 'custom'

export function McpSettings({ gateway, onConfigSaved }: McpSettingsProps): React.JSX.Element {
  const t = strings
  const m = t.settings.mcp
  const { config, setConfig, patch } = useRunnerConfig(m.failedLoad)
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)

  useEffect(() => {
    if (!config) {
      return
    }

    const first = Object.keys(getServers(config)).sort()[0] ?? null
    setSelected(first)
  }, [config])

  const servers = useMemo(() => getServers(config), [config])
  const serversRef = useLatestRef(servers)
  const names = useMemo(() => Object.keys(servers).sort(), [servers])

  useDeepLinkHighlight({
    block: 'nearest',
    elementId: serverName => `mcp-server-${serverName}`,
    onResolve: setSelected,
    param: 'server',
    ready: serverName => Boolean(config) && serverName in servers
  })

  useEffect(() => {
    const server = selected ? serversRef.current[selected] : null

    setName(selected ?? '')
    setBody(JSON.stringify(server ?? EMPTY_SERVER, null, 2))
  }, [selected, serversRef])

  if (!config) {
    return <LoadingState label={m.loading} />
  }

  const mutateAndSave = async (nextServers: McpServers): Promise<void> => {
    const result = await patch({ path: ['mcp_servers'], value: nextServers })

    if (!result.ok) {
      throw new Error(result.error)
    }

    const next = setIn(config, ['mcp_servers'], nextServers)
    setConfig(next)
  }

  const saveServer = async () => {
    const nextName = name.trim()

    if (!nextName) {
      notify({ kind: 'error', title: m.nameRequiredTitle, message: m.nameRequiredMessage })

      return
    }

    let parsed: Record<string, unknown>

    try {
      const raw = JSON.parse(body)

      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error(m.objectRequired)
      }

      parsed = raw as Record<string, unknown>
    } catch (err) {
      notifyError(err, m.invalidJson)

      return
    }

    setSaving(true)

    try {
      const nextServers = { ...servers }

      if (selected && selected !== nextName) {
        delete nextServers[selected]
      }

      nextServers[nextName] = parsed

      await mutateAndSave(nextServers)
      setSelected(nextName)
      onConfigSaved?.()

      notify({ kind: 'success', title: m.savedTitle, message: m.savedMessage(nextName) })
    } catch (err) {
      notifyError(err, m.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  const removeServer = async (serverName: string) => {
    setSaving(true)

    try {
      const nextServers = { ...servers }
      delete nextServers[serverName]

      await mutateAndSave(nextServers)
      setSelected(Object.keys(nextServers).sort()[0] ?? null)
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, m.removeFailed)
    } finally {
      setSaving(false)
    }
  }

  const reloadMcp = async () => {
    setReloading(true)

    try {
      if (gateway) {
        await gateway.request('reload.mcp', {
          confirm: true
        })
      } else if (typeof window.deskagent?.reloadMcp === 'function') {
        await window.deskagent.reloadMcp()
      } else {
        notify({ kind: 'warning', title: m.gatewayUnavailableTitle, message: m.gatewayUnavailableMessage })

        return
      }

      notify({ kind: 'success', title: m.reloadedTitle, message: m.reloadedMessage })
    } catch (err) {
      notifyError(err, m.reloadFailed)
    } finally {
      setReloading(false)
    }
  }

  return (
    <SettingsContent>
      <div className="mb-4 flex items-center justify-end gap-4">
        <Button onClick={() => setSelected(null)} size="xs" variant="text">
          {m.newServer}
        </Button>
        <Button disabled={reloading} onClick={() => void reloadMcp()} size="xs" variant="text">
          {reloading ? m.reloading : m.reload}
        </Button>
      </div>

      <div className="grid min-h-0 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <div className="min-h-64">
          {names.length === 0 ? (
            <EmptyState description={m.emptyDesc} title={m.emptyTitle} />
          ) : (
            <div className="grid gap-0.5">
              {names.map(serverName => {
                const server = servers[serverName]
                const active = selected === serverName

                return (
                  <button
                    className={cn(
                      'scroll-mt-2 rounded-md px-2 py-2 text-left transition-colors hover:bg-(--chrome-action-hover)',
                      active ? 'bg-(--ui-bg-tertiary) text-foreground' : 'text-muted-foreground'
                    )}
                    id={`mcp-server-${serverName}`}
                    key={serverName}
                    onClick={() => setSelected(serverName)}
                    type="button"
                  >
                    <div className="truncate text-sm font-medium">{serverName}</div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <Pill>{transportLabel(server)}</Pill>
                      {server.disabled === true && <Pill>{m.disabled}</Pill>}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <div className="grid content-start gap-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Wrench className="size-4 text-muted-foreground" />
            {selected ? m.editServer : m.newServer}
          </div>
          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">{m.name}</span>
            <Input onChange={event => setName(event.currentTarget.value)} placeholder="filesystem" value={name} />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">{m.serverJson}</span>
            <Textarea
              className="min-h-80 font-mono text-xs"
              onChange={event => setBody(event.currentTarget.value)}
              spellCheck={false}
              value={body}
            />
          </label>
          <div className="flex items-center justify-between">
            {selected ? (
              <Button
                className="text-destructive hover:text-destructive"
                disabled={saving}
                onClick={() => void removeServer(selected)}
                size="xs"
                variant="text"
              >
                {m.remove}
              </Button>
            ) : (
              <span />
            )}
            <Button disabled={saving} onClick={() => void saveServer()} size="sm">
              {saving ? t.common.saving : m.saveServer}
            </Button>
          </div>
        </div>
      </div>
    </SettingsContent>
  )
}
