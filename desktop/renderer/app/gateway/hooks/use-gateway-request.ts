import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import type { DeskAgentGateway } from '@/shared/deskagent'
import { resolveGatewayWsUrl } from '@/shared/lib/gateway-ws-url'
import { $gateway, $gatewayState, setConnection } from '@/shared/store/gateway'

export function useGatewayRequest() {
  const gatewayState = useStore($gatewayState)
  const gatewayRef = useRef<DeskAgentGateway | null>(null)

  const connectionRef = useRef<Awaited<ReturnType<NonNullable<typeof window.deskagent>['getConnection']>> | null>(
    null
  )

  const gatewayStateRef = useRef(gatewayState)
  const reconnectingRef = useRef<Promise<DeskAgentGateway | null> | null>(null)
  useEffect(() => {
    gatewayStateRef.current = gatewayState
  }, [gatewayState])

  // Track the active gateway so outbound requests and overlay props always
  // target the focused socket.
  useEffect(
    () =>
      $gateway.subscribe(gateway => {
        gatewayRef.current = gateway as DeskAgentGateway | null
      }),
    []
  )

  const ensureGatewayOpen = useCallback(async () => {
    const existing = gatewayRef.current

    if (!existing) {
      return null
    }

    if (gatewayStateRef.current === 'open') {
      return existing
    }

    if (reconnectingRef.current) {
      return reconnectingRef.current
    }

    reconnectingRef.current = (async () => {
      const desktop = window.deskagent

      if (!desktop) {
        return null
      }

      try {
        const conn = await desktop.getConnection()
        connectionRef.current = conn
        setConnection(conn)
        // Re-mint the WS URL before reconnecting. OAuth tickets are single-use
        // and short-lived, so the cached conn.wsUrl ticket is dead here;
        // resolveGatewayWsUrl() throws a reauth error in OAuth mode rather than
        // connecting with a stale ticket. Stash it so requestGateway can show
        // the actionable "sign in again" message.
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await existing.connect(wsUrl)

        return existing
      } catch {
        connectionRef.current = null
        setConnection(null)

        return null
      } finally {
        reconnectingRef.current = null
      }
    })()

    return reconnectingRef.current
  }, [])

  // Filesystem-bound calls handled by the desktop main process instead of
  // the backend. The backend runs in Docker and can't access the user disk,
  // so these would otherwise return -32601. Returns the local result, or
  // ``null`` to fall through to the WS gateway.
  const tryLocalIntercept = useCallback(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    const desktop = window.deskagent

    if (method === 'config.get') {
      if (params.key !== 'project') {
        return null
      }

      const cwd = typeof params.cwd === 'string' ? params.cwd : ''

      if (!desktop?.gitBranch || !cwd) {
        return { cwd, branch: '' }
      }

      try {
        const result = await desktop.gitBranch(cwd)

        return { cwd, branch: result.branch ?? '' }
      } catch {
        return { cwd, branch: '' }
      }
    }

    if (method === 'complete.path') {
      if (!desktop?.completePath) {
        return { items: [] }
      }

      const word = typeof params.word === 'string' ? params.word : ''
      const cwd = typeof params.cwd === 'string' ? params.cwd : undefined

      try {
        const out = await desktop.completePath({ word, cwd })
        const prefixMatch = /^(@[a-z]+:)/i.exec(word)
        const prefix = prefixMatch ? prefixMatch[1] : '@file:'

        const items = (out.items as { label: string; value: string }[]).map(item => ({
          text: `${prefix}${item.label}`,
          display: item.label,
          meta: item.value
        }))

        return { items }
      } catch {
        return { items: [] }
      }
    }

    return null
  }, [])

  const requestGateway = useCallback(
    async <T>(method: string, params: Record<string, unknown> = {}) => {
      const intercepted = await tryLocalIntercept(method, params)

      if (intercepted !== null) {
        return intercepted as T
      }

      const gateway = gatewayRef.current

      if (!gateway) {
        throw new Error('DeskAgent gateway unavailable')
      }

      try {
        return await gateway.request<T>(method, params)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)

        if (!/not connected|connection closed/i.test(message)) {
          throw error
        }

        // Remote gateways re-mint a single-use OAuth ticket on each connect,
        // so a stale ticket surfaces as "connection closed" and we retry once
        // through the local reconnect path before giving up.
        const recovered = await ensureGatewayOpen()

        if (!recovered) {
          throw error
        }

        return recovered.request<T>(method, params)
      }
    },
    [ensureGatewayOpen, tryLocalIntercept]
  )

  return { connectionRef, gatewayRef, requestGateway }
}
