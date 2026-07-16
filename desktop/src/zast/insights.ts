import type { InsightsOverview } from '@/types/zast'

/** Fetch the user's usage overview from `GET /api/insights/overview?days=N`.
 *
 * ``window.zastDesktop.api`` only forwards ``path``/``method``/``body`` (no
 * ``query`` field) — see ``electron/ipc/connection.cjs`` and ``ZastApiRequest``
 * in ``global.d.ts``. The ``days`` parameter is therefore inlined into the
 * path string here.
 */
export function getInsightsOverview({ days = 30 }: { days?: number } = {}): Promise<InsightsOverview> {
  return window.zastDesktop.api<InsightsOverview>({
    path: `/api/insights/overview?days=${days}`
  })
}
