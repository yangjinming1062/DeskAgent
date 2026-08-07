import { atom } from 'nanostores'
import { ApiClient } from '../api'

export interface UserInfo {
  id: number
  username: string
}

const STORED_TOKEN = localStorage.getItem('da.client.token')
const STORED_BASE = localStorage.getItem('da.client.baseUrl') || 'http://localhost:8000'

export const $authToken = atom<string | null>(STORED_TOKEN)
export const $baseUrl = atom<string>(STORED_BASE)
export const $user = atom<UserInfo | null>(null)
export const $authState = atom<'loading' | 'authenticated' | 'unauthenticated'>(
  STORED_TOKEN ? 'loading' : 'unauthenticated'
)

export function setAuth(token: string, baseUrl: string, user: UserInfo): void {
  $authToken.set(token)
  $baseUrl.set(baseUrl)
  $user.set(user)
  $authState.set('authenticated')
  localStorage.setItem('da.client.token', token)
  localStorage.setItem('da.client.baseUrl', baseUrl)
}

export function clearAuth(): void {
  $authToken.set(null)
  $user.set(null)
  $authState.set('unauthenticated')
  localStorage.removeItem('da.client.token')
}

export function getApiClient(): ApiClient {
  return new ApiClient($baseUrl.get(), () => $authToken.get())
}
