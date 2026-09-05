import { useEffect, useMemo, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { TOOLSET_CATALOG, type ToolsetCatalogEntry } from '@/shared/lib/toolset-catalog'
import { EmptyState, LoadingBlock, Pill, SearchField, SettingsSubsection, Toggle } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

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

export function ToolsetsPage(): React.JSX.Element {
  const t = strings
  const sk = t.skills
  const toolsetText = t.toolsets

  const loadErrorLabelRef = useLatestRef(sk.skillsLoadFailed)
  const saveErrorLabelRef = useLatestRef(sk.toolsetsRefreshFailed)

  const loader = useAsyncLoader<ToolsetRosterEntry[]>(async () => {
    const res = await window.spiritagent.toolsets.list()

    if (!res.ok) {
      notifyError(res.error ?? 'load-failed', loadErrorLabelRef.current)

      throw new Error(res.error ?? 'toolsets list failed')
    }

    return res.toolsets ?? []
  })

  const [toolsets, setToolsets] = useState<ToolsetRosterEntry[]>([])
  const loading = loader.isLoading
  const loadFailed = loader.error !== null

  useEffect(() => {
    if (loader.data) {
      setToolsets(loader.data)
    }
  }, [loader.data])

  const [searchTerm, setSearchTerm] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)

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

      const texts = (toolsetText as Record<string, { description: string; label: string }>)[entry.id] ?? {
        description: '',
        label: entry.id
      }

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
      const res = await window.spiritagent.toolsets.setEnabled({ id, enabled: nextEnabled })

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
    return <LoadingBlock label={sk.loading} />
  }

  if (loadFailed && toolsets.length === 0) {
    return (
      <SettingsSubsection title={sk.tabToolsets}>
        <EmptyState description={sk.loadFailedDesc} title={sk.loadFailedTitle} />
      </SettingsSubsection>
    )
  }

  if (visibleEntries.length === 0) {
    return (
      <SettingsSubsection title={sk.tabToolsets}>
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
      <SettingsSubsection title={sk.tabToolsets}>
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
            <div className="rounded-xl border border-line-hairline bg-surface-card p-4" key={catalog.id}>
              <div className="flex items-start gap-3">
                <Icon className="mt-0.5 size-5 shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium text-strong">{label || catalog.id}</div>
                  <div className="mt-1 text-[10px] leading-relaxed text-faint">{description}</div>
                </div>
                <Toggle
                  checked={enabled}
                  disabled={savingId === catalog.id}
                  onChange={value => void toggle(catalog.id, value)}
                />
              </div>
              {toolNames.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5 pl-8">
                  {toolNames.map(name => (
                    <span
                      className="rounded-md border border-line-standard bg-fill-faint px-1.5 py-0.5 font-mono text-[0.65rem] text-muted"
                      key={name}
                    >
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
