import { useEffect, useRef } from 'react'

import type { ZastConnection } from '@/global'
import { translateNow } from '@/i18n'
import { resolveGatewayWsUrl } from '@/lib/gateway-ws-url'
import { reconnectBackoffMs } from '@/lib/reconnect'
import { logout } from '@/store/auth'
import { applyDesktopBootProgress, completeDesktopBoot, failDesktopBoot, setDesktopBootStep } from '@/store/boot'
import { reportPrimaryGatewayState, setConnection, setPrimaryGateway, setRunnerOnline, tearDownPrimaryGateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'
import type { RpcEvent } from '@/types/zast'
import { ZastGateway } from '@/zast'

// Backend uses WS close 1008 for auth failures (token expired/revoked) —
// trigger logout instead of looping reconnect with a dead token.
const WS_CLOSE_POLICY_VIOLATION = 1008

async function syncRunnerTools(gateway: ZastGateway): Promise<void> {
  const desktop = window.zastDesktop

  if (!desktop?.runnerGetTools) {
    return
  }

  try {
    const tools = await desktop.runnerGetTools()

    const names = tools
      .map((t: { function?: { name?: string }; name?: string }) => t.function?.name || t.name)
      .filter(Boolean) as string[]

    const hasFileTools = names.includes('read_file') || names.includes('list_directory')
    console.log(
      `[tools.sync] runner reported ${tools.length} tools${
        names.length
          ? ` (read_file=${names.includes('read_file')}, list_directory=${names.includes('list_directory')})`
          : ''
      }`
    )

    if (names.length) {
      console.log(`[tools.sync] names: ${names.join(', ')}`)
    }

    if (!hasFileTools) {
      console.warn('[tools.sync] LLM will lack file tools in this session')
    }

    if (tools.length > 0) {
      const result = await gateway.request<{ count: number }>('tools.sync', { tools })
      console.log(`[tools.sync] backend accepted count=${result?.count}`)
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error)
    console.error(`[tools.sync] failed: ${msg}`)
  }
}

interface GatewayBootOptions {
  handleGatewayEvent: (event: RpcEvent) => void
  onConnectionReady: (
    connection: Awaited<ReturnType<NonNullable<typeof window.zastDesktop>['getConnection']>> | null
  ) => void
  onGatewayReady: (gateway: ZastGateway | null) => void
}

export function useGatewayBoot({ handleGatewayEvent, onConnectionReady, onGatewayReady }: GatewayBootOptions) {
  const callbacksRef = useRef({ handleGatewayEvent, onConnectionReady, onGatewayReady })

  callbacksRef.current = { handleGatewayEvent, onConnectionReady, onGatewayReady }

  useEffect(() => {
    let cancelled = false
    const desktop = window.zastDesktop

    const publish = (next: ZastConnection | null) => {
      callbacksRef.current.onConnectionReady(next)
      setConnection(next)
    }

    if (!desktop) {
      failDesktopBoot('Desktop IPC bridge is unavailable.')

      return () => void (cancelled = true)
    }

    // macOS sleep silently drops the renderer's WebSocket. The backend Python
    // process keeps running, but nothing re-opened the socket on wake, so the
    // app stayed disabled forever on "Starting…". Once the initial boot
    // succeeds we treat any non-open state as recoverable and reconnect with
    // backoff, and we nudge a reconnect on the OS/browser signals that fire
    // around wake (power resume, network online, the window becoming visible).
    let bootCompleted = false
    let bootOverlayDismissed = false
    let reconnecting = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempt = 0
    // Surface "sign in again" once per disconnect episode, not on every backoff
    // tick — a stale OAuth ticket fails every attempt and would otherwise stack
    // identical error toasts (and their haptics). Reset on the next clean open.
    let reauthNotified = false

    const dismissOverlayOnce = () => {
      if (bootOverlayDismissed) {
        return
      }

      bootOverlayDismissed = true
      completeDesktopBoot()
    }

    const gatewayOpen = () => gateway.connectionState === 'open'

    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
    }

    const attemptReconnect = async () => {
      if (cancelled || reconnecting || gatewayOpen()) {
        return
      }

      reconnecting = true

      try {
        const conn = await desktop.getConnection()

        if (cancelled) {
          return
        }

        publish(conn)
        // Re-mint the WS URL before reconnecting. OAuth tickets are single-use
        // with a short TTL, so the ticket baked into the cached conn.wsUrl is
        // dead on every reconnect after the initial boot — reusing it surfaces
        // as an opaque "Could not connect to gateway". resolveGatewayWsUrl
        // mints a fresh ticket (or throws a reauth error in OAuth mode rather
        // than connecting with a stale one). For local/token gateways the URL
        // carries a long-lived token and the re-mint is a cheap no-op.
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncRunnerTools(gateway)
        reconnectAttempt = 0
      } catch {
        // Transport failure — fall through to the backoff in the finally block.
      } finally {
        reconnecting = false

        if (!cancelled && !gatewayOpen()) {
          scheduleReconnect()
        }
      }
    }

    function scheduleReconnect() {
      if (cancelled || reconnecting || reconnectTimer !== null || gatewayOpen()) {
        return
      }

      const delay = reconnectBackoffMs(reconnectAttempt)
      reconnectAttempt += 1
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void attemptReconnect()
      }, delay)
    }

    const reconnectNow = () => {
      if (cancelled || !bootCompleted) {
        return
      }

      clearReconnectTimer()
      reconnectAttempt = 0

      if (!gatewayOpen()) {
        void attemptReconnect()
      }
    }

    const offBootProgress = desktop.onBootProgress(payload => applyDesktopBootProgress(payload))
    void desktop
      .getBootProgress()
      .then(snapshot => applyDesktopBootProgress(snapshot))
      .catch(() => undefined)

    setDesktopBootStep({
      phase: 'renderer.boot',
      message: translateNow('boot.steps.startingDesktopConnection'),
      progress: 6
    })

    const gateway = new ZastGateway()
    callbacksRef.current.onGatewayReady(gateway)
    setPrimaryGateway(gateway)

    const offState = gateway.onState(st => {
      reportPrimaryGatewayState(st)

      if (st === 'open') {
        reconnectAttempt = 0
        reauthNotified = false
        clearReconnectTimer()

        // On a normal post-wake reconnect, nothing calls completeDesktopBoot()
        // afterwards, so dismiss the boot-progress overlay here once we're open
        // again — otherwise it sticks. A no-op on the initial boot.
        if (bootCompleted) {
          dismissOverlayOnce()
        }
      } else if (bootCompleted && (st === 'closed' || st === 'error')) {
        if (st === 'closed' && gateway.lastCloseCode === WS_CLOSE_POLICY_VIOLATION) {
          void logout()

          return
        }

        scheduleReconnect()
      }
    })

    const offEvent = gateway.onEvent(event => callbacksRef.current.handleGatewayEvent(event))

    const offPowerResume = desktop.onPowerResume?.(() => reconnectNow())

    const onOnline = () => reconnectNow()

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        reconnectNow()
      }
    }

    window.addEventListener('online', onOnline)
    document.addEventListener('visibilitychange', onVisible)

    const offWindowState = desktop.onWindowStateChanged?.(payload => {
      void payload
    })

    const offRunnerStatus = desktop.onRunnerStatus?.(ev => {
      if (ev.type === 'running' || ev.type === 'tools_changed') {
        setRunnerOnline(true)

        if (gateway.connectionState === 'open') {
          void syncRunnerTools(gateway)
        }
      } else if (ev.type === 'stopped' || ev.type === 'error') {
        setRunnerOnline(false)
      }
    })

    async function boot() {
      try {
        const conn = await desktop.getConnection()

        if (cancelled) {
          return
        }

        setDesktopBootStep({
          phase: 'renderer.gateway.connect',
          message: translateNow('boot.steps.connectingGateway'),
          progress: 95
        })
        publish(conn)
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncRunnerTools(gateway)
        dismissOverlayOnce()
        bootCompleted = true
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err)
          failDesktopBoot(message)
          notifyError(err, translateNow('boot.errors.desktopBootFailed'))
        }
      }
    }

    void boot()

    return () => {
      cancelled = true
      clearReconnectTimer()
      window.removeEventListener('online', onOnline)
      document.removeEventListener('visibilitychange', onVisible)
      offPowerResume?.()
      offState()
      offEvent()
      offWindowState?.()
      offRunnerStatus?.()
      offBootProgress()
      publish(null)
      callbacksRef.current.onGatewayReady(null)
      tearDownPrimaryGateway()
    }
  }, [])
}
