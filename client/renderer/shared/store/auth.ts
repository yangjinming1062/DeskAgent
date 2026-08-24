import type { DesktopAuthBroadcast, DesktopAuthSnapshot } from '@ipc/contracts'
import { atom } from 'nanostores'

import { clearAllGltf } from '@/companion/3d/gltf-instance-cache'

import { tearDownPrimaryGateway } from './gateway'

export type AuthState =
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
      $auth.set({ kind: 'unauthenticated' })
    }
  } catch (error) {
    $auth.set({
      error: error instanceof Error ? error.message : String(error),
      kind: 'unauthenticated'
    })
  }
}

// 应用主进程 → 渲染层的鉴权广播（登录 / 登出 / 刷新）。精灵窗口不会运行登录表单，
// 因此依赖此广播来感知新会话。登出时的网关拆除由 GatewayBooter 卸载时处理
//（按 $auth 做条件渲染），这里只翻转鉴权状态。
export function applyAuthBroadcast(payload: DesktopAuthBroadcast): void {
  const { snapshot } = payload

  if (payload.authenticated && snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot)) {
    $auth.set({ kind: 'authenticated', snapshot })
  } else {
    clearAllGltf()
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
    clearAllGltf()
    $auth.set({ kind: 'unauthenticated' })
  }
}
