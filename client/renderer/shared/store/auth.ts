import type { DesktopAuthBroadcast, DesktopAuthSnapshot } from '@ipc/contracts'
import { atom } from 'nanostores'

import { clearCompanionStorage } from '@/shared/lib/storage'

import { tearDownPrimaryGateway } from './gateway'

type AuthState =
  | { kind: 'pending' }
  | { error?: string; kind: 'unauthenticated' }
  | { kind: 'authenticated'; snapshot: DesktopAuthSnapshot }

export const $auth = atom<AuthState>({ kind: 'pending' })

function isExpiredSnapshot(snapshot: DesktopAuthSnapshot | null | undefined): boolean {
  const expiresAt = snapshot?.tokenExpiresAt

  return typeof expiresAt !== 'number' || !Number.isFinite(expiresAt) || expiresAt <= Date.now()
}

export async function hydrateAuth(): Promise<void> {
  try {
    const snapshot = await window.spiritagent.getSession()

    if (snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot)) {
      $auth.set({ kind: 'authenticated', snapshot })
    } else {
      await clearCompanionStorage()
      $auth.set({ kind: 'unauthenticated' })
    }
  } catch (error) {
    await clearCompanionStorage()
    $auth.set({
      error: error instanceof Error ? error.message : String(error),
      kind: 'unauthenticated'
    })
  }
}

// 应用主进程 → 渲染层的鉴权广播（登录 / 登出 / 刷新）。精灵窗口不会运行登录表单，
// 因此依赖此广播来感知新会话。登出时的网关拆除由 GatewayBooter 卸载时处理
//（按 $auth 做条件渲染），这里只翻转鉴权状态。
//
// 函数声明 async 让 applyAuthBroadcast 路径里 clearCompanionStorage 与 $auth.set 严格
// 串行：之前 fire-and-forget 会让 $auth 立即翻为 unauthenticated，而 OPFS / 本地
// localStorage 还在清。新用户的 hydrate 在清完之前读 localStorage / OPFS 会拿到旧用户
// 残留（renderMode / 模型缓存），产生跨会话串味。代价是广播回调延迟 ~5–30ms（一次
// localStorage.removeItem + OPFS 目录枚举 + removeEntry），仍在合理范围。
export async function applyAuthBroadcast(payload: DesktopAuthBroadcast): Promise<void> {
  const { snapshot } = payload

  if (payload.authenticated && snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot)) {
    $auth.set({ kind: 'authenticated', snapshot })
  } else {
    // 仅在「从 authenticated → unauthenticated」时清存储：token 过期首启动（pending → unauthenticated）
    // 不该抹除已登录用户留下的窗口/面板偏好；clearCompanionStorage 是登出副作用，不是
    // 鉴权初始化的副作用。
    if ($auth.get().kind === 'authenticated') {
      await clearCompanionStorage()
    }

    $auth.set({ kind: 'unauthenticated' })
  }
}

export async function activate(payload: { code: string }): Promise<void> {
  try {
    const snapshot = await window.spiritagent.activate(payload)
    $auth.set({ kind: 'authenticated', snapshot })
  } catch (error) {
    $auth.set({
      error: error instanceof Error ? error.message : String(error),
      kind: 'unauthenticated'
    })
    throw error
  }
}

export async function refreshSession(): Promise<void> {
  // 刷新失败并不会自动登出（与 login() 不同）：旧 JWT 可能仍然有效，
  // 是否把错误抛出由调用方决定。
  const snapshot = await window.spiritagent.refreshSession()
  $auth.set({ kind: 'authenticated', snapshot })
}

export async function logout(): Promise<void> {
  try {
    await window.spiritagent.logout()
  } finally {
    tearDownPrimaryGateway()
    // 不在这里调 clearCompanionStorage：IPC logout 返回后主进程会广播
    // authenticated:false，applyAuthBroadcast 那条路径才是清空的唯一入口。
    // 重复调用会被 idempotent handler 吞掉，但避免双触发 React 重渲染。
    $auth.set({ kind: 'unauthenticated' })
  }
}
