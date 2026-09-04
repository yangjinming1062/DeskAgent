// 工作台运行轨迹：本轮工具调用、最新输出与本会话工件

import { useStore } from '@nanostores/react'
import type React from 'react'

import { ChatMediaCard } from '@/chat/chat-media-card'
import { X } from '@/shared/lib/icons'
import type { ChatMediaItem } from '@/shared/types/spiritagent'
import { $artifacts, $isRailOpen, $runRound, setRailOpen, toggleRail } from '@/workbench/run-rail-store'

import styles from './workbench.module.css'

export function RunRail(): React.JSX.Element {
  const open = useStore($isRailOpen)
  const round = useStore($runRound)
  const artifacts = useStore($artifacts)

  // 用户手动折叠右栏后保留 0 宽，仅一个唤起条；展开恢复 320。
  if (!open) {
    return (
      <aside className={styles.runRailCollapsed}>
        <button
          aria-label="展开运行轨迹"
          className={styles.runRailExpand}
          onClick={() => setRailOpen(true)}
          type="button"
        >
          <span className={styles.runRailExpandLabel}>运行轨迹</span>
        </button>
      </aside>
    )
  }

  return (
    <aside className={styles.runRail}>
      <header className={styles.runRailHeader}>
        <div>
          <h3 className={styles.runRailTitle}>运行轨迹</h3>
          <span className={styles.runRailSubtitle}>{round ? `${round.steps.length} 步 · 本轮` : '空闲中'}</span>
        </div>
        <button
          aria-label="折叠运行轨迹"
          className={styles.runRailCollapse}
          onClick={() => toggleRail()}
          title="折叠"
          type="button"
        >
          <X className="size-3.5" />
        </button>
      </header>

      <div className={styles.runRailBody}>
        <Section subtitle={round?.steps.length ? `${round.steps.length} 步` : '尚未动手'} title="本轮工具">
          {round && round.steps.length > 0 ? (
            <ol className={styles.steps}>
              {round.steps.map((step, idx) => (
                <li className={`${styles.step} ${step.active ? styles.stepActive : ''}`} key={`${step.name}-${idx}`}>
                  <span className={styles.stepIndex}>{idx + 1}</span>
                  <span className={styles.stepName}>{step.name}</span>
                  {step.active ? <span aria-hidden="true" className={styles.stepPulse} /> : null}
                </li>
              ))}
            </ol>
          ) : (
            <p className={styles.sectionEmpty}>{round?.active ? '准备中…' : '这一轮还没动手'}</p>
          )}
        </Section>

        <Section subtitle={round?.active ? '正在执行' : '已落定'} title="最新输出">
          <LatestOutput active={Boolean(round?.active)} media={round?.outputMedia} text={round?.outputText ?? ''} />
        </Section>

        <Section subtitle={artifacts.length ? `${artifacts.length} 项` : '尚无'} title="本会话工件">
          {artifacts.length > 0 ? (
            <div className={styles.artifacts}>
              {artifacts.map(artifact => (
                <ChatMediaCard item={{ type: artifact.kind, url: artifact.url }} key={artifact.id} />
              ))}
            </div>
          ) : (
            <p className={styles.sectionEmpty}>这一轮还没有生成图或视频</p>
          )}
        </Section>
      </div>
    </aside>
  )
}

interface SectionProps {
  children: React.ReactNode
  subtitle?: string
  title: string
}

function Section({ children, subtitle, title }: SectionProps): React.JSX.Element {
  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <h4 className={styles.sectionTitle}>{title}</h4>
        {subtitle ? <span className={styles.sectionSubtitle}>{subtitle}</span> : null}
      </header>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  )
}

interface LatestOutputProps {
  active: boolean
  media?: ChatMediaItem[]
  text: string
}

function LatestOutput({ active, media, text }: LatestOutputProps): React.JSX.Element {
  const trimmed = text.trim()
  const showStreamingHint = active && !trimmed && !media?.length

  const textPreview = trimmed.length > 800 ? `${trimmed.slice(0, 800)}…` : trimmed

  if (showStreamingHint) {
    return <p className={styles.sectionEmpty}>正在准备输出…</p>
  }

  return (
    <div className={styles.outputStack}>
      {textPreview ? (
        <pre className={styles.outputText} data-empty={!trimmed}>
          {textPreview}
        </pre>
      ) : null}
      {media?.length ? (
        <div className={styles.outputMedia}>
          {media.map(m => (
            <ChatMediaCard item={{ type: m.type, url: m.url }} key={m.url} />
          ))}
        </div>
      ) : null}
    </div>
  )
}
