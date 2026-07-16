import { useEffect, useMemo, useRef, useState } from 'react'

import { SearchField } from '@/components/ui/search-field'
import { Switch } from '@/components/ui/switch'
import { TextTab, TextTabMeta } from '@/components/ui/text-tab'
import { useI18n } from '@/i18n'
import { Sparkles } from '@/lib/icons'
import { refreshSession } from '@/store/auth'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'

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

export function SkillsSettings() {
  const { t } = useI18n()
  const s = t.settings.skills
  const sk = t.skills
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [loadFailed, setLoadFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  const loadErrorLabelRef = useRef(s.loadError)
  loadErrorLabelRef.current = s.loadError

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const res = await window.zastDesktop.skills.list()

        if (cancelled) {
          return
        }

        if (res.ok) {
          setSkills(res.skills ?? [])
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

  const skillsRef = useRef(skills)
  skillsRef.current = skills
  const saveErrorRef = useRef(s.saveError)
  saveErrorRef.current = s.saveError
  const refreshErrorRef = useRef(s.refreshError)
  refreshErrorRef.current = s.refreshError

  // Read latest skills via ref so a toggle's identity isn't tied to array
  // length; the IPC failure path rolls back to the pre-click snapshot.
  const toggle = async (name: string, nextEnabled: boolean) => {
    const prev = skillsRef.current

    try {
      const res = await window.zastDesktop.skills.setEnabled({ name, enabled: nextEnabled })

      if (!res.ok || !res.skills) {
        setSkills(prev)
        notifyError(res.error ?? 'save-failed', saveErrorRef.current)

        return
      }

      setSkills(res.skills)

      // Drop the WS *before* refreshing the JWT so any in-flight tool.result
      // is aborted before the server marks the old LoginRecord inactive.
      $gateway.get()?.close()

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
