import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { resolveGatewayWsUrl } from '@/shared/lib/gateway-ws-url'
import type { SpiritAgentGateway } from '@/shared/spiritagent'
import { $gateway, $gatewayState } from '@/shared/store/gateway'

export interface UseGatewayRequestResult {
  gatewayRef: React.RefObject<SpiritAgentGateway | null>
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useGatewayRequest(): UseGatewayRequestResult {
  const gatewayState = useStore($gatewayState)
  const gatewayRef = useRef<SpiritAgentGateway | null>(null)
  const gatewayStateRef = useLatestRef(gatewayState)
  const reconnectingRef = useRef<Promise<SpiritAgentGateway | null> | null>(null)

  // Track the active gateway so outbound requests and overlay props always
  // target the focused socket.
  useEffect(
    () =>
      $gateway.subscribe(gateway => {
        gatewayRef.current = gateway as SpiritAgentGateway | null
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
      const desktop = window.spiritagent

      if (!desktop) {
        return null
      }

      try {
        const conn = await desktop.getConnection()
        // Re-mint the WS URL before reconnecting. OAuth tickets are single-use
        // and short-lived, so the cached conn.wsUrl ticket is dead here;
        // resolveGatewayWsUrl() throws a reauth error in OAuth mode rather than
        // connecting with a stale ticket.
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await existing.connect(wsUrl)

        return existing
      } catch {
        return null
      } finally {
        reconnectingRef.current = null
      }
    })()

    return reconnectingRef.current
  }, [gatewayStateRef])

  const requestGateway = useCallback(
    async <T>(method: string, params: Record<string, unknown> = {}) => {
      const gateway = gatewayRef.current

      if (!gateway) {
        throw new Error('SpiritAgent gateway unavailable')
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
    [ensureGatewayOpen]
  )

  return { gatewayRef, requestGateway }
}
