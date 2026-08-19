import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $desktopBoot } from '@/companion/boot-store'
import { $gatewayState } from '@/shared/store/gateway'

import { useGatewayBoot } from './use-gateway-boot'

// 端到端级的"远程 VPS → 一直卡在 CONNECTING、设置打不开"问题复现：
// 跑的是真实的 useGatewayBoot hook 与真实的 SpiritAgentGateway，
// 配合一只我们完全可控的伪 WebSocket。不依赖 Docker / 真端口——
// 从客户端视角看，"远程 VPS" 就是一只首次连上之后再次重连就被拒的 WebSocket，
// 我们恰好（也仅）伪造这一行为。
//
// 之前的测试（gateway-connecting-overlay.test.tsx）手动设置 stores
// 并断言 overlay；本测试证明 HOOK 真的会产生那个 store 组合，
// 关上"靠读代码推断"的缺口，覆盖启动后重连循环。

type Listener = (ev: unknown) => void

// 只实现 json-rpc-gateway.connect() 会用到的最小 WebSocket 替身：
// readyState、add/removeEventListener('open'|'error'|'close')、close()。
class FakeWebSocket {
  static OPEN = 1
  static CLOSED = 3
  // 由测试切换：'open' = 下一次 socket 连接成功；'fail' = 下一次 socket 出错
  // （远端死亡）。模拟第一次连接后 VPS 下线的场景。
  static mode: 'open' | 'fail' = 'open'
  static instances: FakeWebSocket[] = []

  readyState = 0
  private listeners: Record<string, Set<Listener>> = {}

  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
    const willOpen = FakeWebSocket.mode === 'open'
    // 推到下一个 microtask / macrotask 再解析，保证 connect() 内部的 promise 装配
    // 已经就绪后再触发 open/error（贴合真实 socket 的异步握手）。
    setTimeout(() => {
      if (willOpen) {
        this.readyState = FakeWebSocket.OPEN
        this.emit('open', {})
      } else {
        this.readyState = FakeWebSocket.CLOSED
        this.emit('error', {})
      }
    }, 0)
  }

  addEventListener(type: string, fn: Listener) {
    ;(this.listeners[type] ??= new Set()).add(fn)
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners[type]?.delete(fn)
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
    this.emit('close', {})
  }

  // 主动断开一个 open 状态的 socket，模拟睡眠中的笔记本或远端重启。
  drop() {
    this.readyState = FakeWebSocket.CLOSED
    this.emit('close', {})
  }

  private emit(type: string, ev: unknown) {
    for (const fn of this.listeners[type] ?? []) {
      fn(ev)
    }
  }
}

function fakeDesktop() {
  const conn = {
    baseUrl: 'https://vps.example.com',
    token: 't',
    wsUrl: 'wss://vps.example.com/api/ws?token=t'
  }

  return {
    getConnection: vi.fn(async () => conn),
    getGatewayWsUrl: vi.fn(async () => conn.wsUrl),
    getBootProgress: vi.fn(async () => ({
      error: null,
      fakeMode: false,
      message: '',
      phase: 'init',
      progress: 0,
      running: true,
      timestamp: Date.now()
    })),
    onBootProgress: vi.fn(() => () => undefined),
    onPowerResume: vi.fn(() => () => undefined),
    onWindowStateChanged: vi.fn(() => () => undefined)
  }
}

function Harness() {
  useGatewayBoot({
    handleGatewayEvent: () => undefined,
    onConnectionReady: () => undefined,
    onGatewayReady: () => undefined
  })

  return null
}

const originalWebSocket = globalThis.WebSocket

beforeEach(() => {
  vi.useFakeTimers()
  FakeWebSocket.mode = 'open'
  FakeWebSocket.instances = []
  ;(globalThis as { WebSocket: unknown }).WebSocket = FakeWebSocket
  ;(window as { spiritagent?: unknown }).spiritagent = fakeDesktop()
  $gatewayState.set('idle')
  $desktopBoot.set({
    error: null,
    fakeMode: false,
    message: '',
    phase: 'init',
    progress: 0,
    running: true,
    timestamp: Date.now(),
    visible: true
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  ;(globalThis as { WebSocket: unknown }).WebSocket = originalWebSocket
  delete (window as { spiritagent?: unknown }).spiritagent
})

// 让挂起的 microtask（await）和排队的 0ms socket open/error 全部跑完。
async function flushAsync() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

// 把指数退避推满一个封顶周期，让下一次重连尝试真正执行
// （1s、2s、4s、8s、15s、15s…）。在该次异步工作稳定后返回。
async function advanceBackoff() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000)
  })
}

describe('useGatewayBoot remote reconnect loop (real hook, fake socket)', () => {
  it('INITIAL boot against a dead VPS: getConnection hangs (waitForSpiritAgent) → app sits in the connecting combo, then fails', async () => {
    // 报告中的实际路径：冷启动并指向一台不可达的 VPS。
    // startSpiritAgent() 走远程分支时会在 waitForSpiritAgent() 上挂 45 秒再抛错，
    // 所以渲染端的 `await desktop.getConnection()` 整个窗口内都处于 pending。
    // 在此期间：gatewayState 仍是 'idle'（从未进入 connect），
    // boot.error 为 null → connecting=true → 全屏 CONNECTING 浮层
    // 被锁住，挡住设置入口。
    let rejectConn: (e: Error) => void = () => undefined
    const desktop = fakeDesktop()
    desktop.getConnection = vi.fn(
      () =>
        new Promise((_resolve, reject) => {
          rejectConn = reject
        })
    )
    ;(window as { spiritagent?: unknown }).spiritagent = desktop

    render(<Harness />)
    await flushAsync()

    // getConnection 仍处于 pending——在死掉的 VPS 上干等。没有任何 socket 被创建，
    // gatewayState 始终停在 idle，boot.error 为 null。
    expect(FakeWebSocket.instances).toHaveLength(0)
    expect($gatewayState.get()).not.toBe('open')
    expect($desktopBoot.get().error).toBeNull()
    // ^ 这里 connecting === true → 全屏 CONNECTING 浮层，挡住设置入口。

    // 等约 45 秒，waitForSpiritAgent 放弃，getConnection reject → boot() catch
    // → failDesktopBoot → 启动 BootFailureOverlay 的恢复界面。
    await act(async () => {
      rejectConn(new Error('SpiritAgent backend did not become ready: timeout'))
      await vi.advanceTimersByTimeAsync(0)
    })

    expect($desktopBoot.get().error).toBeTruthy()
  })

  it('a remote that drops post-boot keeps looping with NO boot.error (the dead-end CONNECTING combo)', async () => {
    render(<Harness />)
    await flushAsync()

    // 初次启动已建立连接。
    expect($gatewayState.get()).toBe('open')
    expect($desktopBoot.get().error).toBeNull()
    expect(FakeWebSocket.instances).toHaveLength(1)

    // 远程 VPS 下线：丢掉当前 socket，之后每次重连都让它失败。
    FakeWebSocket.mode = 'fail'
    act(() => FakeWebSocket.instances[0].drop())
    await flushAsync()

    // 在升级阈值前（<6 次尝试，最初约 15 秒）跑几轮退避。
    // 这段窗口内 stock 与修复版行为一致：socket 断开、hook 在重试、
    // gatewayState 非 open、boot.error 仍为 null → CONNECTING 铺满屏幕，
    // 没有任何恢复入口。过了约 45 秒修复版才会置 boot.error，
    // 这点在下一个测试里断言。
    await advanceBackoff()

    expect($gatewayState.get()).not.toBe('open')
    expect($desktopBoot.get().error).toBeNull()
    // 它在主动重试，并非空转——已经创建了更多 socket。
    expect(FakeWebSocket.instances.length).toBeGreaterThan(1)
  })
})
