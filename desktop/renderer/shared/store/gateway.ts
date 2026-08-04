import { atom } from 'nanostores'

import type { DeskAgentGateway } from '@/shared/deskagent'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import type { DeskAgentConnection } from '@/shared/types/global'

export interface RunnerCapabilities {
  microphone?: boolean
  screen_capture?: boolean
  local_stt?: boolean
  local_tts?: boolean
  system_activity?: boolean
  platform?: string
  python?: string
}

// Tracks whether the local Runner is online and has synced its tools — lets
// the message-stream handler fast-fail tool.call instead of parking on the
// 300s IPC future.
export const $runnerOnline = atom(false)
export const $runnerCapabilities = atom<RunnerCapabilities | null>(null)

export function setRunnerOnline(online: boolean): void {
  $runnerOnline.set(online)
}

export function setRunnerCapabilities(capabilities: RunnerCapabilities | null): void {
  $runnerCapabilities.set(capabilities)
}

// Single primary gateway — desktop talks to one Backend.

// The active gateway instance, exposed for inline message-stream components
// (e.g. model overlays) that call gateway methods without the instance
// threaded down through props.
export const $gateway = atom<DeskAgentGateway | null>(null)

// Live backend connection snapshot + WS state. Consumed by the gateway hooks
// and the connecting overlay; owned here (not in a session store) because they
// describe the single backend link, independent of any conversation.
export const $connection = atom<DeskAgentConnection | null>(null)
export const $gatewayState = atom<ConnectionState>('idle')

export function setConnection(next: DeskAgentConnection | null): void {
  $connection.set(next)
}

export function setGatewayState(next: ConnectionState): void {
  $gatewayState.set(next)
}

export function setPrimaryGateway(gateway: DeskAgentGateway | null): void {
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
