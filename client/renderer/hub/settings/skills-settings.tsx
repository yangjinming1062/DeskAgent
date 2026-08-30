import { useEffect, useMemo, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { CHIP_FILTER, CHIP_FILTER_ACTIVE, SearchField, Toggle } from '@/shared/panel'
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
    const res = await window.spiritagent.skills.list()

    if (!res.ok) {
      notifyError(res.error ?? 'load-failed', loadErrorLabelRef.current)

      throw new Error(res.error ?? 'skills list failed')
    }

    return res.skills ?? []
  })

  const [skills, setSkills] = useState<SkillSummary[]>([])
  const loading = loader.isLoading
  const loadFailed = loader.error !== null

  // loader.data 同步到本地状态——挂载时 loader 是真相源，本地写入之后优先
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

  // 通过 ref 读取最新的 skills，使切换回调不依赖数组长度；
  // IPC 失败路径回滚到点击前的快照。
  const toggle = async (name: string, nextEnabled: boolean) => {
    const prev = skillsRef.current

    try {
      const res = await window.spiritagent.skills.setEnabled({ name, enabled: nextEnabled })

      if (!res.ok || !res.skills) {
        setSkills(prev)
        notifyError(res.error ?? 'save-failed', saveErrorRef.current)

        return
      }

      setSkills(res.skills)

      // 刷新 JWT 以获取新 skill 权限；不关精灵窗口的 WS（权限变更在服务端完成）
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

    // 与平台不兼容的 skills 从菜单中完全隐藏；skills.cjs 中的 IPC 守卫
    // 会拒绝旧调用方重新启用它们。
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
      <div className="space-y-1.5">
        {selectedSkills.map(skill => (
          <ListRow
            action={<Toggle checked={skill.enabled} onChange={value => void toggle(skill.name, value)} />}
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
                        ? 'text-xs font-medium text-body capitalize'
                        : 'text-xs font-medium text-body'
                    }
                  >
                    {categoryLabel(categoryKey, sk.other)}
                  </h3>
                  <div className="space-y-1.5">
                    {items.map(skill => (
                      <ListRow
                        action={<Toggle checked={skill.enabled} onChange={value => void toggle(skill.name, value)} />}
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
      <SettingsSubsection intro={s.intro} title={s.title}>
        {!noSkillsAtAll && (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                className={selectedCategory === null ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
                onClick={() => setSelectedCategory(null)}
                type="button"
              >
                {sk.all} · {visibleCount}
              </button>
              {orderedCategories.map(categoryKey => (
                <button
                  className={selectedCategory === categoryKey ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
                  key={categoryKey}
                  onClick={() => setSelectedCategory(categoryKey)}
                  type="button"
                >
                  <span className={isUserCategory(categoryKey) ? 'capitalize' : undefined}>
                    {categoryLabel(categoryKey, sk.other)}
                  </span>{' '}
                  · {counts.get(categoryKey) ?? 0}
                </button>
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
