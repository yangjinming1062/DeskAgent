import { atom } from 'nanostores'
import { GatewayClient } from '../gateway/GatewayClient'
import type { GatewayState } from '../gateway/types'

export const $gatewayState = atom<GatewayState>('idle')
export const gateway = new GatewayClient()

let unsub: (() => void) | null = null
export function initGatewayStateSync(): void {
  if (unsub) return
  unsub = gateway.onState(s => $gatewayState.set(s))
}
