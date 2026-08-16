import { atom } from 'nanostores'

import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import type { SpiritAgentGateway } from '@/shared/spiritagent'

// Single primary gateway — desktop talks to one Backend.

// The active gateway instance, exposed for inline message-stream components
// (e.g. model overlays) that call gateway methods without the instance
// threaded down through props.
export const $gateway = atom<SpiritAgentGateway | null>(null)

// Live WS state for the active gateway. Consumed by the gateway hooks and
// the connecting overlay; owned here (not in a session store) because it
// describes the single backend link, independent of any conversation.
export const $gatewayState = atom<ConnectionState>('idle')

export function setGatewayState(next: ConnectionState): void {
  $gatewayState.set(next)
}

export function setPrimaryGateway(gateway: SpiritAgentGateway | null): void {
  $gateway.set(gateway)
  setGatewayState(gateway?.connectionState ?? 'closed')
}

// Closes the active gateway (synchronous WS teardown so 401-driven logout
// doesn't wait for TCP timeout) and clears the atom. Both `auth.ts::logout`
// and `use-gateway-boot.ts` cleanup share this pair; keeping it in one place
// makes the close-then-clear ordering impossible to split.
export function tearDownPrimaryGateway(): void {
  $gateway.get()?.close()
  setPrimaryGateway(null)
}

export function reportPrimaryGatewayState(state: ConnectionState): void {
  setGatewayState(state)
}
