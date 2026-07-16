import { atom } from 'nanostores'

import type { ConnectionState } from '@/lib/gateway-protocol'
import { setGatewayState } from '@/store/session'
import type { ZastGateway } from '@/zast'

// Tracks whether the local Runner is online and has synced its tools — lets
// the message-stream handler fast-fail tool.call instead of parking on the
// 300s IPC future.
export const $runnerOnline = atom(false)

export function setRunnerOnline(online: boolean): void {
  $runnerOnline.set(online)
}

// Single primary gateway — desktop talks to one Backend. The previous
// multi-profile pool (secondaries Map, prune/reconnect helpers) was removed
// with the profile subsystem.

// The active gateway instance, exposed for inline message-stream components
// (e.g. model overlays) that call gateway methods without the instance
// threaded down through props.
export const $gateway = atom<ZastGateway | null>(null)

export function setPrimaryGateway(gateway: ZastGateway | null): void {
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
