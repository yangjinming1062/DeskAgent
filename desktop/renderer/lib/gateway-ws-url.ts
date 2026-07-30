import type { DeskAgentConnection } from '@/global'

export interface ResolveGatewayWsUrlDeps {
  getGatewayWsUrl?: () => Promise<string>
}

export async function resolveGatewayWsUrl(
  desktop: ResolveGatewayWsUrlDeps,
  conn: Pick<DeskAgentConnection, 'wsUrl'>
): Promise<string> {
  const mint = desktop.getGatewayWsUrl

  if (mint) {
    const fresh = await mint().catch(() => null)

    if (fresh) {
      return fresh
    }
  }

  return conn.wsUrl
}
