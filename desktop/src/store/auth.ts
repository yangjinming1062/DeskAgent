import { atom } from 'nanostores'

import type { DesktopAuthSnapshot } from '@/global'

import { setRunnerOnline, tearDownPrimaryGateway } from './gateway'

export type AuthState =
  | { kind: 'pending' }
  | { kind: 'unauthenticated'; error?: string }
  | { kind: 'authenticated'; snapshot: DesktopAuthSnapshot }

export const $auth = atom<AuthState>({ kind: 'pending' })

export async function hydrateAuth(): Promise<void> {
  try {
    const snapshot = await window.deskagent.getSession()
    const expiresAt = snapshot?.tokenExpiresAt
    const expired = typeof expiresAt !== 'number' || !Number.isFinite(expiresAt) || expiresAt <= Date.now()

    if (snapshot && snapshot.hasToken && !expired) {
      $auth.set({ kind: 'authenticated', snapshot })
    } else {
      $auth.set({ kind: 'unauthenticated' })
    }
  } catch (error) {
    $auth.set({
      kind: 'unauthenticated',
      error: error instanceof Error ? error.message : String(error)
    })
  }
}

export async function login(payload: { username: string; password: string }): Promise<void> {
  try {
    const snapshot = await window.deskagent.login(payload)
    $auth.set({ kind: 'authenticated', snapshot })
  } catch (error) {
    $auth.set({
      kind: 'unauthenticated',
      error: error instanceof Error ? error.message : String(error)
    })
    throw error
  }
}

export async function refreshSession(payload?: any): Promise<void> {
  // Refresh failure does NOT auto-logout (unlike login()): the old JWT may
  // still work, and the caller can decide whether to surface the error.
  const snapshot = await window.deskagent.refreshSession(payload)
  $auth.set({ kind: 'authenticated', snapshot })
}

export async function logout(): Promise<void> {
  try {
    await window.deskagent.logout()
  } finally {
    tearDownPrimaryGateway()
    // Reset Runner online state — a stale `true` would defeat the tool-call
    // fast-fail for the next user on the same Electron profile.
    setRunnerOnline(false)
    $auth.set({ kind: 'unauthenticated' })
  }
}
