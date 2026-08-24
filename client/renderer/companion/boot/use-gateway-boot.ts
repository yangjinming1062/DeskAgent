import type { SpiritAgentConnection } from '@ipc/contracts'
import { useEffect, useRef } from 'react'

import { startAutonomyProvision, stopAutonomyProvision } from '@/companion/autonomy'
import {
  applyDesktopBootProgress,
  completeDesktopBoot,
  failDesktopBoot,
  setDesktopBootStep
} from '@/companion/boot-store'
import { $chatSessionId, hydrateChatMessages, setChatSession } from '@/companion/chat-store'
import { $effectiveTier, $spriteState, setSpriteState } from '@/companion/companion-store'
import { clearVfx, emitVfx } from '@/companion/mesh2d/mesh2d-vfx'
import { openMainSession } from '@/companion/session-list-store'
import { resolveGatewayWsUrl } from '@/shared/lib/gateway-ws-url'
import { log } from '@/shared/lib/log'
import { reconnectBackoffMs } from '@/shared/lib/reconnect'
import { SpiritAgentGateway } from '@/shared/spiritagent'
import { logout } from '@/shared/store/auth'
import { reportPrimaryGatewayState, setPrimaryGateway, tearDownPrimaryGateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { RpcEvent, SessionResumeResponse } from '@/shared/types/spiritagent'

// 后端对鉴权失败（token 过期 / 被吊销）使用 WS close 1008——
// 此时触发登出，而不是用无效 token 不断重连。
const WS_CLOSE_POLICY_VIOLATION = 1008

// 每次（重）开后重新上报档位：后端存在进程级字典里，后端重启会静默清空。
// 推生效值（不只是 user_preferred），让沉浸式聚焦上下文能跨重连接存活——
// 否则新的 WS 握手会让后端短暂解静音，而用户其实还在 IDE / 全屏窗口里。
// 即发即忘。
function syncDisturbanceTier(gateway: SpiritAgentGateway): void {
  const tier = $effectiveTier.get()

  if (!tier) {
    return
  }

  void gateway.request('companion.set_disturbance_tier', { tier }).catch(() => {})
}

async function syncRunnerTools(gateway: SpiritAgentGateway): Promise<void> {
  const desktop = window.spiritagent

  if (!desktop?.runnerGetTools) {
    return
  }

  try {
    const tools = await desktop.runnerGetTools()

    const names = tools
      .map((t: { function?: { name?: string }; name?: string }) => t.function?.name || t.name)
      .filter(Boolean) as string[]

    const hasFileTools = names.includes('read_file') || names.includes('list_directory')

    if (!hasFileTools) {
      log.warn('gateway-boot', 'tools.sync: LLM will lack file tools in this session')
    }

    if (tools.length > 0) {
      await gateway.request<{ count: number }>('tools.sync', { tools })
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error)
    log.error('gateway-boot', `tools.sync failed: ${msg}`)
  }
}

interface GatewayBootOptions {
  handleGatewayEvent: (event: RpcEvent) => void
  onConnectionReady: (connection: SpiritAgentConnection | null) => void
  onGatewayReady: (gateway: SpiritAgentGateway | null) => void
}

export function useGatewayBoot({ handleGatewayEvent, onConnectionReady, onGatewayReady }: GatewayBootOptions): void {
  const callbacksRef = useRef({ handleGatewayEvent, onConnectionReady, onGatewayReady })

  callbacksRef.current = { handleGatewayEvent, onConnectionReady, onGatewayReady }

  useEffect(() => {
    let cancelled = false
    const desktop = window.spiritagent

    const publish = (next: SpiritAgentConnection | null) => {
      callbacksRef.current.onConnectionReady(next)
    }

    if (!desktop) {
      failDesktopBoot('Desktop IPC bridge is unavailable.')

      return () => void (cancelled = true)
    }

    // macOS 睡眠会静默丢掉渲染端的 WebSocket。后端 Python 进程仍在跑，
    // 但唤醒时没人重开 socket，应用永远卡在 "Starting…"。一旦初次启动成功，
    // 我们就把任何非 open 状态视为可恢复，按退避重连，
    // 并在唤醒相关的 OS / 浏览器信号（电源恢复、网络上线、窗口变为可见）触发
    // 时主动 nudge 一次重连。
    let bootCompleted = false
    let bootOverlayDismissed = false
    let reconnecting = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let graceTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempt = 0

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

    const clearGraceTimer = () => {
      if (graceTimer !== null) {
        clearTimeout(graceTimer)
        graceTimer = null
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
        // 重连前重新生成 WS URL。OAuth 票据是一次性的且 TTL 很短，
        // 所以缓存 conn.wsUrl 里那条票据在初次启动后的每次重连都已失效——
        // 复用只会换来一条神秘的"无法连接到网关"。resolveGatewayWsUrl 会签发
        // 新票据（OAuth 模式下会抛 reauth 错误，而不是拿着过期票据硬连）。
        // local/token 网关的 URL 携带的是长效 token，重新签发是廉价的空操作。
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncRunnerTools(gateway)
        reconnectAttempt = 0
      } catch {
        // 传输失败——交给 finally 块里的退避逻辑处理。
      } finally {
        reconnecting = false

        if (!cancelled && !gatewayOpen()) {
          scheduleReconnect()
        }
      }
    }

    function scheduleReconnect(): void {
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
      message: strings.boot.steps.startingDesktopConnection,
      progress: 6
    })

    const gateway = new SpiritAgentGateway()
    callbacksRef.current.onGatewayReady(gateway)
    setPrimaryGateway(gateway)

    const offState = gateway.onState(st => {
      reportPrimaryGatewayState(st)

      if (st === 'open') {
        reconnectAttempt = 0
        clearReconnectTimer()
        clearGraceTimer()
        // 重新上报打扰档位，让后端进程级字典在初次启动以及每次重连后
        // 都拿到用户持久化下来的选择（覆盖后端重启、OAuth 重新登录）。
        syncDisturbanceTier(gateway)
        startAutonomyProvision()

        // 断连阶段挂的 sleep_zzz 气泡需要主动清掉，否则即便精灵已经"醒来"
        // 头顶的 z 字符仍会停留到自然 expiry。
        clearVfx('sleep_zzz')

        // 正常的唤醒后重连不会再次调用 completeDesktopBoot()，
        // 所以这里在再次 open 后把启动进度浮层收掉——否则它会一直挂着。
        // 初次启动时是 no-op。
        if (bootCompleted) {
          dismissOverlayOnce()
          // 重连后"打起精神"：如果之前表达过 disconnected 降级，就回到 idle
          // （plan §4.5）。若从未显示过降级，则静默恢复。
          const cur = $spriteState.get()

          if (cur === 'disconnected') {
            setSpriteState('idle', { force: true })
          }

          // 重新挂载已有会话，避免下一次 prompt.submit 触发"找不到 session"。
          // 后端每次 WS 断开都会清空内存里的 runtime_sessions；
          // session.resume 从持久化的 DB 会话记录里把运行时重新派生出来。
          const sid = $chatSessionId.get()

          const syncMountSeq = (res: SessionResumeResponse) => {
            if (typeof res.current_seq === 'number') {
              gateway.resetSeq(res.current_seq)
            }
          }

          if (sid) {
            void gateway
              .request<SessionResumeResponse>('session.resume', {
                session_id: sid,
                last_seq: gateway.lastReceivedSeq
              })
              .then(res => {
                if (!res.resumed) {
                  syncMountSeq(res)

                  if (Array.isArray(res.messages)) {
                    hydrateChatMessages(res.messages)
                  }
                }
              })
              .catch(() => {
                setChatSession(null)
                void openMainSession(syncMountSeq)
              })
          } else {
            void openMainSession(syncMountSeq)
          }
        }
      } else if (bootCompleted && (st === 'closed' || st === 'error')) {
        if (st === 'closed' && gateway.lastCloseCode === WS_CLOSE_POLICY_VIOLATION) {
          void logout()

          return
        }

        // 安排断连宽限状态；超时则固定进入 disconnected，重连前不再做额外升级。
        if (graceTimer === null) {
          const isForeground = document.visibilityState === 'visible'
          const graceMs = isForeground ? 3000 : 30000
          graceTimer = setTimeout(() => {
            graceTimer = null
            setSpriteState('disconnected')
            // DESIGN §6.5：犯困/走神 → 挂载 sleep_zzz 气泡；重连后由 onState='open'
            // 分支的 clearGraceTimer 后面逻辑清理（先发个 wakeUp 再让 3D/2D 自然复位）。
            emitVfx('sleep_zzz', { nx: 0.5, ny: 0.05 })
          }, graceMs)
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

    const offRunnerStatus = desktop.onRunnerStatus?.(ev => {
      if (ev.type === 'running' || ev.type === 'runner_ready') {
        if (gateway.connectionState === 'open') {
          void syncRunnerTools(gateway)
        }
      }
    })

    async function boot(): Promise<void> {
      try {
        const conn = await desktop.getConnection()

        if (cancelled) {
          return
        }

        setDesktopBootStep({
          phase: 'renderer.gateway.connect',
          message: strings.boot.steps.connectingGateway,
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
          notifyError(err, strings.boot.errors.desktopBootFailed)
        }
      }
    }

    void boot()

    return () => {
      cancelled = true
      clearReconnectTimer()
      clearGraceTimer()
      window.removeEventListener('online', onOnline)
      document.removeEventListener('visibilitychange', onVisible)
      offPowerResume?.()
      offState()
      offEvent()
      offRunnerStatus?.()
      offBootProgress()
      stopAutonomyProvision()
      publish(null)
      callbacksRef.current.onGatewayReady(null)
      tearDownPrimaryGateway()
    }
  }, [])
}
