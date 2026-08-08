import { useEffect, useMemo, useState } from 'react'

import { SearchField, Switch, TextTab, TextTabMeta } from '@/shared/components/ui'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { Sparkles } from '@/shared/lib/icons'
import { refreshSession } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { UNCATEGORIZED_KEY } from './constants'
import { EmptyState, ListRow, LoadingState, SettingsSubsection } from './primitives'

type SkillSummary = {
  category?: string
  name: string
  description?: string
  platforms?: string[] | null
  compatible: boolean
  enabled: boolean
}

function categoryLabel(key: string, otherLabel: string): string {
  return key === UNCATEGORIZED_KEY ? otherLabel : key.replace(/-/g, ' ')
}

function isUserCategory(key: string): boolean {
  return key !== UNCATEGORIZED_KEY
}

export function SkillsSettings(): React.JSX.Element {
  const t = strings
  const s = t.settings.skills
  const sk = t.skills
  const loadErrorLabel = s.loadError
  const loadErrorLabelRef = useLatestRef(loadErrorLabel)

  const loader = useAsyncLoader<SkillSummary[]>(async () => {
    const res = await window.deskagent.skills.list()

    if (!res.ok) {
      notifyError(res.error ?? 'load-failed', loadErrorLabelRef.current)

      return []
    }

    return res.skills ?? []
  })

  const [skills, setSkills] = useState<SkillSummary[]>([])
  const loading = loader.isLoading
  const loadFailed = loader.error !== null

  // Mirror loader.data into local state so optimistic-toggle `setSkills` works
  // — the loader is the source of truth at mount, local writes win afterwards.
  useEffect(() => {
    if (loader.data) {
      setSkills(loader.data)
    }
  }, [loader.data])

  const [searchTerm, setSearchTerm] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  const skillsRef = useLatestRef(skills)
  const saveErrorRef = useLatestRef(s.saveError)
  const refreshErrorRef = useLatestRef(s.refreshError)

  // Read latest skills via ref so a toggle's identity isn't tied to array
  // length; the IPC failure path rolls back to the pre-click snapshot.
  const toggle = async (name: string, nextEnabled: boolean) => {
    const prev = skillsRef.current

    try {
      const res = await window.deskagent.skills.setEnabled({ name, enabled: nextEnabled })

      if (!res.ok || !res.skills) {
        setSkills(prev)
        notifyError(res.error ?? 'save-failed', saveErrorRef.current)

        return
      }

      setSkills(res.skills)

      // Refresh the JWT for the new skill-scoped permissions. Don't close the sprite's
      // WS — it's owned by the companion and the permission delta is server-side.
      try {
        await refreshSession()
      } catch (err) {
        notifyError(err, refreshErrorRef.current)
      }
    } catch (err) {
      setSkills(prev)
      notifyError(err, saveErrorRef.current)
    }
  }

  const { counts, orderedCategories, groupedVisible, visibleCount } = useMemo(() => {
    const counts = new Map<string, number>([[UNCATEGORIZED_KEY, 0]])
    const needle = searchTerm.trim().toLowerCase()
    const groupedVisible = new Map<string, SkillSummary[]>()
    let visibleCount = 0

    // Platform-incompatible skills are hidden from the menu entirely; the IPC
    // guard in skills.cjs refuses re-enabling them for older callers.
    for (const skill of skills) {
      if (!skill.compatible) {
        continue
      }

      visibleCount += 1
      const key = skill.category ?? UNCATEGORIZED_KEY
      counts.set(key, (counts.get(key) ?? 0) + 1)

      if (selectedCategory !== null && key !== selectedCategory) {
        continue
      }

      if (needle) {
        const matches =
          skill.name.toLowerCase().includes(needle) ||
          (skill.description ?? '').toLowerCase().includes(needle) ||
          (skill.category ?? '').toLowerCase().includes(needle)

        if (!matches) {
          continue
        }
      }

      const list = groupedVisible.get(key)

      if (list) {
        list.push(skill)
      } else {
        groupedVisible.set(key, [skill])
      }
    }

    const orderedCategories = Array.from(counts.entries())
      .filter(([, n]) => n > 0)
      .sort(([a], [b]) => {
        if (!isUserCategory(a)) {
          return 1
        }

        if (!isUserCategory(b)) {
          return -1
        }

        return a.localeCompare(b)
      })
      .map(([key]) => key)

    return { counts, orderedCategories, groupedVisible, visibleCount }
  }, [skills, searchTerm, selectedCategory])

  if (loading) {
    return <LoadingState label={s.loading} />
  }

  const noSkillsAtAll = skills.length === 0
  const showLoadFailed = loadFailed && noSkillsAtAll
  const showEmptyInstall = !loadFailed && noSkillsAtAll
  const allHiddenByPlatform = !noSkillsAtAll && visibleCount === 0
  const showFilterEmpty = !noSkillsAtAll && !allHiddenByPlatform && groupedVisible.size === 0

  let body: React.ReactNode

  if (showLoadFailed) {
    body = <EmptyState description={sk.loadFailedDesc} title={sk.loadFailedTitle} />
  } else if (showEmptyInstall) {
    body = <EmptyState description={s.emptyDesc} title={s.emptyTitle} />
  } else if (allHiddenByPlatform) {
    body = <EmptyState description={s.hiddenByPlatformDesc} title={s.hiddenByPlatformTitle} />
  } else if (showFilterEmpty) {
    body = <EmptyState description={sk.noSkillsDesc} title={sk.noSkillsTitle} />
  } else if (selectedCategory !== null) {
    const selectedSkills = groupedVisible.get(selectedCategory) ?? []

    body = (
      <div className="divide-y divide-border/30 rounded-md border border-border/30 bg-card">
        {selectedSkills.map(skill => (
          <ListRow
            action={<Switch checked={skill.enabled} onCheckedChange={value => void toggle(skill.name, value)} />}
            description={skill.description || sk.noDescription}
            key={skill.name}
            title={skill.name}
          />
        ))}
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-6">
        {orderedCategories.flatMap(categoryKey => {
          const items = groupedVisible.get(categoryKey)

          return items
            ? [
                <div className="flex flex-col gap-2" key={categoryKey}>
                  <h3
                    className={
                      isUserCategory(categoryKey)
                        ? 'text-sm font-medium text-foreground capitalize'
                        : 'text-sm font-medium text-foreground'
                    }
                  >
                    {categoryLabel(categoryKey, sk.other)}
                  </h3>
                  <div className="divide-y divide-border/30 rounded-md border border-border/30 bg-card">
                    {items.map(skill => (
                      <ListRow
                        action={
                          <Switch checked={skill.enabled} onCheckedChange={value => void toggle(skill.name, value)} />
                        }
                        description={skill.description || sk.noDescription}
                        key={skill.name}
                        title={skill.name}
                      />
                    ))}
                  </div>
                </div>
              ]
            : []
        })}
      </div>
    )
  }

  return (
    <div>
      <SettingsSubsection icon={Sparkles} intro={s.intro} title={s.title}>
        {!noSkillsAtAll && (
          <>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <TextTab active={selectedCategory === null} onClick={() => setSelectedCategory(null)}>
                {sk.all}
                <TextTabMeta>{visibleCount}</TextTabMeta>
              </TextTab>
              {orderedCategories.map(categoryKey => (
                <TextTab
                  active={selectedCategory === categoryKey}
                  key={categoryKey}
                  onClick={() => setSelectedCategory(categoryKey)}
                >
                  <span className={isUserCategory(categoryKey) ? 'capitalize' : undefined}>
                    {categoryLabel(categoryKey, sk.other)}
                  </span>
                  <TextTabMeta>{counts.get(categoryKey) ?? 0}</TextTabMeta>
                </TextTab>
              ))}
            </div>
            <div className="mt-3">
              <SearchField
                aria-label={sk.searchSkills}
                onChange={setSearchTerm}
                placeholder={sk.searchSkills}
                value={searchTerm}
              />
            </div>
          </>
        )}
      </SettingsSubsection>
      {!noSkillsAtAll && <div className="mt-6">{body}</div>}
      {noSkillsAtAll && body}
    </div>
  )
}
