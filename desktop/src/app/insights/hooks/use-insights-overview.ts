import { useCallback, useEffect, useState } from 'react'

import type { InsightsOverview } from '@/types/zast'
import { getInsightsOverview } from '@/zast'

export type InsightsDays = 7 | 30 | 90

export interface UseInsightsOverviewResult {
  data: InsightsOverview | null
  days: InsightsDays
  error: string | null
  loading: boolean
  refetch: () => void
  setDays: (days: InsightsDays) => void
}

/** Fetch `GET /api/insights/overview?days=<days>` and re-fetch when the days
 * selector changes. The hook returns the latest response plus a stable
 * refetch handle so the view can wire a manual refresh button.
 */
export function useInsightsOverview(initialDays: InsightsDays = 30): UseInsightsOverviewResult {
  const [days, setDays] = useState<InsightsDays>(initialDays)
  const [data, setData] = useState<InsightsOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [requestId, setRequestId] = useState(0)

  useEffect(() => {
    let cancelled = false

    setLoading(true)
    setError(null)

    getInsightsOverview({ days })
      .then(next => {
        if (cancelled) {
          return
        }

        setData(next)
      })
      .catch(err => {
        if (cancelled) {
          return
        }

        setError(err instanceof Error ? err.message : String(err))
        setData(null)
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [days, requestId])

  const refetch = useCallback(() => {
    setRequestId(value => value + 1)
  }, [])

  return { data, days, error, loading, refetch, setDays }
}
