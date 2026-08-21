export { BrandMark } from './components/brand-mark'
export { ErrorBoundary } from './components/error-boundary'

export { HapticsProvider } from './components/haptics-provider'
export { InlineNotice } from './components/notifications'
export { PageLoader } from './components/page-loader'
export * from './components/ui'
export { useAsyncLoader } from './hooks/use-async-loader'
export { useLatestRef } from './hooks/use-latest-ref'
export { useRouteEnumParam } from './hooks/use-route-enum-param'
export { PAGE_INSET_X } from './layout/page-inset'

export { installClipboardShim } from './lib/clipboard'
export { JsonRpcGatewayClient } from './lib/gateway-protocol'
export type { ConnectionState, GatewayClientOptions, GatewayEvent } from './lib/gateway-protocol'
export { resolveGatewayWsUrl } from './lib/gateway-ws-url'
export { triggerHaptic } from './lib/haptics'
export { isClientErrorIpc } from './lib/ipc-error'
export { log } from './lib/log'
export { buildSecretFieldBody } from './lib/secret-field-body'
export type { SecretFieldBody } from './lib/secret-field-body'
export { TOOLSET_CATALOG } from './lib/toolset-catalog'
export { cn } from './lib/utils'
export { getSpiritAgentConfig, getSpiritAgentConfigDefaults, saveSpiritAgentConfig } from './spiritagent/config'
export { SpiritAgentGateway } from './spiritagent/gateway'
export { $auth, activate, applyAuthBroadcast, hydrateAuth, logout, refreshSession } from './store/auth'

export {
  $gateway,
  $gatewayState,
  reportPrimaryGatewayState,
  setPrimaryGateway,
  tearDownPrimaryGateway
} from './store/gateway'
export { $hapticsMuted } from './store/haptics'
export { $notifications, clearNotifications, dismissNotification, notify, notifyError } from './store/notifications'
export { $runnerPhase, $runnerReady, hydrateRunnerStatus } from './store/runner-status'
export { $desktopVersion, refreshDesktopVersion } from './store/version'

export { strings } from './strings'
export * from './themes'

export type * from './types/global'
export type * from './types/reactions'
export type * from './types/spiritagent'
