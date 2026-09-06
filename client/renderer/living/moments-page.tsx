// 片刻页：时间线，新在上。后端直连；空态文案人格化。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import { resolvePortraitUrl } from '@/companion'

import { $moments, $momentsLoading, hydrateMoments } from './journal-store'
import styles from './moments.module.css'

const DATE_FORMATTER = new Intl.DateTimeFormat('zh-CN')

function formatDate(iso: string): string {
  try {
    return DATE_FORMATTER.format(new Date(iso))
  } catch {
    return iso
  }
}

const KIND_LABELS = {
  emotion: '心情',
  greeting: '问候',
  milestone: '里程碑',
  scene: '房间',
  together: '在一起',
  user: '随笔'
} as const

function getKindLabel(kind: string): string {
  return kind in KIND_LABELS ? KIND_LABELS[kind as keyof typeof KIND_LABELS] : '片刻'
}

function MomentPhoto({ alt, url }: { alt: string; url: string }): React.JSX.Element | null {
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    void resolvePortraitUrl(url).then(resolved => {
      if (!cancelled) {
        setSrc(resolved)
      }
    })

    return () => {
      cancelled = true
    }
  }, [url])

  if (!src) {
    return null
  }

  return (
    <div className={styles.media}>
      <img alt={alt} className={styles.mediaImage} src={src} />
    </div>
  )
}

export function MomentsPage(): React.JSX.Element {
  const moments = useStore($moments)
  const loading = useStore($momentsLoading)
  const [expandedId, setExpandedId] = useState<null | string>(null)

  useEffect(() => {
    void hydrateMoments()
  }, [])

  const formattedMoments = useMemo(
    () =>
      moments.map(m => ({
        ...m,
        displayDate: formatDate(m.createdAt)
      })),
    [moments]
  )

  if (loading && moments.length === 0) {
    return <p className={styles.empty}>正在翻看相册…</p>
  }

  if (moments.length === 0) {
    return <p className={styles.empty}>还没有留下什么片刻。</p>
  }

  return (
    <div className={styles.list}>
      {formattedMoments.map(m => {
        const expanded = expandedId === m.id

        return (
          <button
            className={styles.card}
            key={m.id}
            onClick={() => setExpandedId(expanded ? null : m.id)}
            type="button"
          >
            <div className={styles.cardHeader}>
              <span className={styles.kindBadge}>{getKindLabel(m.kind)}</span>
              <time className={styles.date} dateTime={m.createdAt}>
                {m.displayDate}
              </time>
            </div>
            <h3 className={styles.title}>{m.title ?? '无题'}</h3>
            {m.body && (
              <p className={`${styles.body} ${expanded ? styles.bodyExpanded : styles.bodyClamp}`}>{m.body}</p>
            )}
            {m.mediaUrl ? <MomentPhoto alt={m.title ?? ''} url={m.mediaUrl} /> : null}
          </button>
        )
      })}
    </div>
  )
}
