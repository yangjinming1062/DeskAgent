import { memo, useCallback } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n'
import { RefreshCw } from '@/lib/icons'

import { type InsightsDays, useInsightsOverview } from './hooks/use-insights-overview'

const DAYS_OPTIONS = [
  { id: '7', label: '7d' },
  { id: '30', label: '30d' },
  { id: '90', label: '90d' }
] as const

const BAR_HEIGHT = 96
const BAR_GAP = 2
const MAX_VISIBLE_TAGS = 8
const MAX_TOP_TOOLS = 8
const MAX_PLATFORMS = 6

function formatNumber(value: number): string {
  return value.toLocaleString()
}

interface OverviewCardProps {
  label: string
  value: string
  hint?: string
}

function OverviewCard({ label, value, hint }: OverviewCardProps) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-(--stroke-zast) bg-(--ui-bg-secondary) p-4">
      <span className="text-[0.7rem] uppercase tracking-wide text-(--ui-text-tertiary)">{label}</span>
      <span className="text-2xl font-semibold tabular-nums text-foreground">{value}</span>
      {hint && <span className="text-xs text-(--ui-text-tertiary)">{hint}</span>}
    </div>
  )
}

interface BarRow {
  date: string
  messages: number
}

const ActivityChart = memo(function ActivityChart({ rows }: { rows: BarRow[] }) {
  if (rows.length === 0) {
    return <div className="grid h-32 place-items-center text-xs text-(--ui-text-tertiary)">No activity in window</div>
  }

  const max = Math.max(1, ...rows.map(r => r.messages))

  return (
    <div className="flex h-32 items-end gap-0.5">
      {rows.map(row => {
        const heightPct = (row.messages / max) * 100
        const heightPx = Math.max(2, (heightPct / 100) * (BAR_HEIGHT - 8))

        return (
          <div
            aria-label={`${row.date}: ${row.messages} messages`}
            className="group flex flex-1 flex-col items-center justify-end"
            key={row.date}
            style={{ minWidth: 4 }}
            title={`${row.date}: ${row.messages}`}
          >
            <div
              className="w-full rounded-t-sm bg-primary/70 transition-colors group-hover:bg-primary"
              style={{ height: heightPx, marginBottom: BAR_GAP }}
            />
          </div>
        )
      })}
    </div>
  )
})

export function InsightsView() {
  const { t } = useI18n()
  const i = t.insights
  const { data, days, error, loading, refetch, setDays } = useInsightsOverview(30)

  const handleDaysChange = useCallback(
    (id: string) => {
      setDays(Number(id) as InsightsDays)
    },
    [setDays]
  )

  if (loading && !data) {
    return <PageLoader label={i.loading} />
  }

  if (error && !data) {
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <div>
          <p className="text-sm text-destructive">{error}</p>
          <Button className="mt-4" onClick={refetch} size="sm" variant="outline">
            <RefreshCw className="size-3.5" />
            {i.retry}
          </Button>
        </div>
      </div>
    )
  }

  if (!data) {
    return null
  }

  const o = data.overview
  const avgMinutes = (o.avg_session_duration / 60).toFixed(1)

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight">{i.heading}</h1>
        <div className="flex items-center gap-3">
          <SegmentedControl onChange={handleDaysChange} options={DAYS_OPTIONS} value={String(days)} />
          <Button aria-label={i.refresh} disabled={loading} onClick={refetch} size="icon-sm" variant="outline">
            <RefreshCw className={loading ? 'animate-spin size-3.5' : 'size-3.5'} />
          </Button>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <OverviewCard hint={i.windowHint(days)} label={i.overview.sessions} value={formatNumber(o.total_sessions)} />
        <OverviewCard label={i.overview.messages} value={formatNumber(o.total_messages)} />
        <OverviewCard
          hint={`${formatNumber(o.total_input_tokens)} / ${formatNumber(o.total_output_tokens)}`}
          label={i.overview.tokens}
          value={formatNumber(o.total_tokens)}
        />
        <OverviewCard hint={`${avgMinutes}m avg`} label={i.overview.hours} value={`${o.total_hours.toFixed(1)}h`} />
        <OverviewCard label={i.overview.tools} value={formatNumber(o.total_tool_calls)} />
      </section>

      <section className="mt-6 grid gap-6 md:grid-cols-2">
        <div className="rounded-lg border border-(--stroke-zast) bg-(--ui-bg-secondary) p-4">
          <h2 className="mb-3 text-sm font-medium">{i.topTools}</h2>
          {data.top_tools.length === 0 ? (
            <p className="text-xs text-(--ui-text-tertiary)">{i.empty}</p>
          ) : (
            <ol className="grid gap-1.5 text-sm">
              {data.top_tools.slice(0, MAX_TOP_TOOLS).map(item => (
                <li className="flex items-center justify-between gap-3 font-mono text-xs" key={item.tool}>
                  <span className="truncate">{item.tool}</span>
                  <span className="shrink-0 tabular-nums text-(--ui-text-tertiary)">{formatNumber(item.count)}</span>
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="rounded-lg border border-(--stroke-zast) bg-(--ui-bg-secondary) p-4">
          <h2 className="mb-3 text-sm font-medium">{i.models}</h2>
          {data.models.length === 0 ? (
            <p className="text-xs text-(--ui-text-tertiary)">{i.empty}</p>
          ) : (
            <ul className="grid gap-1.5 text-sm">
              {data.models.map(model => (
                <li className="flex flex-col gap-0.5" key={model.model}>
                  <span className="font-mono text-xs">{model.model}</span>
                  <span className="truncate text-[0.65rem] text-(--ui-text-tertiary)">
                    {model.base_url || i.noBaseUrl}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-(--stroke-zast) bg-(--ui-bg-secondary) p-4">
          <h2 className="mb-3 text-sm font-medium">{i.platforms}</h2>
          {data.platforms.length === 0 ? (
            <p className="text-xs text-(--ui-text-tertiary)">{i.empty}</p>
          ) : (
            <ul className="grid gap-1.5 text-sm">
              {data.platforms.slice(0, MAX_PLATFORMS).map(platform => (
                <li className="flex items-center justify-between gap-3 font-mono text-xs" key={platform.platform}>
                  <span className="truncate">{platform.platform}</span>
                  <span className="shrink-0 tabular-nums text-(--ui-text-tertiary)">
                    {Math.round(platform.pct * 100)}% · {formatNumber(platform.count)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-(--stroke-zast) bg-(--ui-bg-secondary) p-4">
          <h2 className="mb-3 text-sm font-medium">{i.skills}</h2>
          <p className="mb-2 text-xs text-(--ui-text-tertiary)">
            {i.skillsTotal(data.skills.total_memories, data.skills.new_in_window)}
          </p>
          {data.skills.top_tags.length === 0 ? (
            <p className="text-xs text-(--ui-text-tertiary)">{i.empty}</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {data.skills.top_tags.slice(0, MAX_VISIBLE_TAGS).map(tag => (
                <li
                  className="rounded-full bg-(--ui-bg-tertiary) px-2 py-0.5 font-mono text-[0.65rem] text-(--ui-text-secondary)"
                  key={tag.tag}
                >
                  {tag.tag} · {tag.count}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="mt-6 rounded-lg border border-(--stroke-zast) bg-(--ui-bg-secondary) p-4">
        <h2 className="mb-3 text-sm font-medium">{i.activity}</h2>
        <ActivityChart rows={data.activity} />
        {data.activity.length > 0 && (
          <div className="mt-2 flex justify-between text-[0.65rem] text-(--ui-text-tertiary)">
            <span>{data.activity[0]?.date}</span>
            <span>{data.activity[data.activity.length - 1]?.date}</span>
          </div>
        )}
      </section>
    </div>
  )
}
