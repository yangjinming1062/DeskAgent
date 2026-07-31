import { useEffect, useMemo, useRef, useState } from 'react'

import { SearchField } from '@/shared/components/ui/search-field'
import { Switch } from '@/shared/components/ui/switch'
import { Wrench } from '@/shared/lib/icons'
import { TOOLSET_CATALOG, type ToolsetCatalogEntry } from '@/shared/lib/toolset-catalog'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { EmptyState, LoadingState, Pill, SettingsSubsection } from './primitives'

type ToolsetRosterEntry = {
  id: string
  toolNames: string[]
  enabled: boolean
}

type ToolsetView = {
  catalog: ToolsetCatalogEntry
  label: string
  description: string
  roster: ToolsetRosterEntry | undefined
}

export function ToolsetsSettings() {
  const t = strings
  const sk = t.skills
  const toolsetText = t.toolsets

  const [toolsets, setToolsets] = useState<ToolsetRosterEntry[]>([])
  const [loadFailed, setLoadFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)

  const loadErrorLabelRef = useRef(sk.skillsLoadFailed)
  loadErrorLabelRef.current = sk.skillsLoadFailed
  const saveErrorLabelRef = useRef(sk.toolsetsRefreshFailed)
  saveErrorLabelRef.current = sk.toolsetsRefreshFailed

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const res = await window.deskagent.toolsets.list()

        if (cancelled) {
          return
        }

        if (res.ok) {
          setToolsets(res.toolsets ?? [])
          setLoadFailed(false)
        } else {
          setLoadFailed(true)
          notifyError(res.error ?? 'load-failed', loadErrorLabelRef.current)
        }
      } catch (err) {
        if (cancelled) {
          return
        }

        setLoadFailed(true)
        notifyError(err, loadErrorLabelRef.current)
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  const rosterById = useMemo(() => {
    const map = new Map<string, ToolsetRosterEntry>()

    for (const t of toolsets) {
      map.set(t.id, t)
    }

    return map
  }, [toolsets])

  const visibleEntries = useMemo(() => {
    const needle = searchTerm.trim().toLowerCase()

    return TOOLSET_CATALOG.flatMap<ToolsetView>(entry => {
      const roster = rosterById.get(entry.id)
      const texts = toolsetText[entry.id]
      const toolNames = roster?.toolNames ?? []

      const matches =
        !needle ||
        entry.id.toLowerCase().includes(needle) ||
        texts.label.toLowerCase().includes(needle) ||
        texts.description.toLowerCase().includes(needle) ||
        toolNames.some(n => n.toLowerCase().includes(needle))

      return matches ? [{ catalog: entry, label: texts.label, description: texts.description, roster }] : []
    })
  }, [rosterById, searchTerm, toolsetText])

  const enabledCount = useMemo(() => toolsets.filter(t => t.enabled).length, [toolsets])

  const toggle = async (id: string, nextEnabled: boolean) => {
    const prev = toolsets
    setSavingId(id)

    try {
      const res = await window.deskagent.toolsets.setEnabled({ id, enabled: nextEnabled })

      if (!res.ok || !res.toolsets) {
        setToolsets(prev)
        notifyError(res.error ?? 'save-failed', saveErrorLabelRef.current)

        return
      }

      setToolsets(res.toolsets)
    } catch (err) {
      setToolsets(prev)
      notifyError(err, saveErrorLabelRef.current)
    } finally {
      setSavingId(null)
    }
  }

  if (loading) {
    return <LoadingState label={sk.loading} />
  }

  if (loadFailed && toolsets.length === 0) {
    return (
      <SettingsSubsection icon={Wrench} title={sk.tabToolsets}>
        <EmptyState description={sk.loadFailedDesc} title={sk.loadFailedTitle} />
      </SettingsSubsection>
    )
  }

  if (visibleEntries.length === 0) {
    return (
      <SettingsSubsection icon={Wrench} title={sk.tabToolsets}>
        <SearchField
          aria-label={sk.searchToolsets}
          onChange={setSearchTerm}
          placeholder={sk.searchToolsets}
          value={searchTerm}
        />
        <div className="mt-4">
          <EmptyState description={sk.noToolsetsDesc} title={sk.noToolsetsTitle} />
        </div>
      </SettingsSubsection>
    )
  }

  return (
    <div>
      <SettingsSubsection icon={Wrench} title={sk.tabToolsets}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Pill tone="primary">{sk.toolsetsEnabled(enabledCount, toolsets.length)}</Pill>
        </div>
        <div className="mt-3">
          <SearchField
            aria-label={sk.searchToolsets}
            onChange={setSearchTerm}
            placeholder={sk.searchToolsets}
            value={searchTerm}
          />
        </div>
      </SettingsSubsection>

      <div className="mt-6 flex flex-col gap-3">
        {visibleEntries.map(({ catalog, label, description, roster }) => {
          const Icon = catalog.icon
          const enabled = roster?.enabled ?? true
          const toolNames = roster?.toolNames ?? []

          return (
            <div className="rounded-md border border-border/30 bg-card p-4" key={catalog.id}>
              <div className="flex items-start gap-3">
                <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="text-[length:var(--conversation-text-font-size)] font-medium text-foreground">
                    {label || catalog.id}
                  </div>
                  <div className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                    {description}
                  </div>
                </div>
                <Switch
                  checked={enabled}
                  disabled={savingId === catalog.id}
                  onCheckedChange={value => void toggle(catalog.id, value)}
                />
              </div>
              {toolNames.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5 pl-8">
                  {toolNames.map(name => (
                    <span className="rounded-md bg-(--ui-bg-quinary) px-1.5 py-0.5 font-mono text-[0.65rem]" key={name}>
                      {name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
