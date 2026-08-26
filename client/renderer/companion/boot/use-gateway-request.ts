import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { resolveGatewayWsUrl } from '@/shared/lib/gateway-ws-url'
import type { SpiritAgentGateway } from '@/shared/spiritagent'
import { $gateway, $gatewayState } from '@/shared/store/gateway'

interface UseGatewayRequestResult {
  gatewayRef: React.RefObject<SpiritAgentGateway | null>
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useGatewayRequest(): UseGatewayRequestResult {
  const gatewayState = useStore($gatewayState)
  const gatewayRef = useRef<SpiritAgentGateway | null>(null)
  const gatewayStateRef = useLatestRef(gatewayState)
  const reconnectingRef = useRef<Promise<SpiritAgentGateway | null> | null>(null)

  // 跟踪当前活动的 gateway，让出站请求与 overlay props 都打到当前 socket 上。
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
        // 重连前重新签发 WS URL。OAuth 票据是一次性且短期的，
        // 所以缓存 conn.wsUrl 里的票据在此已死；
        // resolveGatewayWsUrl() 在 OAuth 模式下会抛 reauth 错误，
        // 而不是拿着过期票据硬连。
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

        // 远端网关每次连接都会重新签发一次性 OAuth 票据，
        // 因此过期票据会表现为"connection closed"，
        // 我们走一次本地重连路径重试一次再放弃。
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
