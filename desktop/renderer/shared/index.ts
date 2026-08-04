// Public surface of renderer/shared. Imports outside this barrel go via
// `@/shared/<module>`/<file>` directly, never `@shared` to peer module internals.

export { BrandMark } from './components/brand-mark'
export { ErrorBoundary } from './components/error-boundary'

export { HapticsProvider } from './components/haptics-provider'
export { InlineNotice, NotificationStack } from './components/notifications'
export { PageLoader } from './components/page-loader'
export * from './components/ui'
export {
  getDeskAgentConfig,
  getDeskAgentConfigDefaults,
  getDeskAgentConfigRecord,
  saveDeskAgentConfig
} from './deskagent/config'
export { DeskAgentGateway } from './deskagent/gateway'
export { useMediaQuery } from './hooks/use-media-query'

export { useIsMobile } from './hooks/use-mobile'
export { useRouteEnumParam } from './hooks/use-route-enum-param'

export { PAGE_INSET_X } from './layout/page-inset'
export { installClipboardShim } from './lib/clipboard'
export { JsonRpcGatewayClient } from './lib/gateway-protocol'
export type { ConnectionState, GatewayClientOptions, GatewayEvent } from './lib/gateway-protocol'
export { resolveGatewayWsUrl } from './lib/gateway-ws-url'
export { triggerHaptic } from './lib/haptics'

export { queryClient } from './lib/query-client'

export { TOOLSET_CATALOG } from './lib/toolset-catalog'
export { cn } from './lib/utils'
export { $auth, applyAuthBroadcast, hydrateAuth, login, logout, refreshSession } from './store/auth'

export {
  $gateway,
  $gatewayState,
  reportPrimaryGatewayState,
  setConnection,
  setPrimaryGateway,
  setRunnerOnline,
  tearDownPrimaryGateway
} from './store/gateway'
export { $hapticsMuted } from './store/haptics'
export { $notifications, clearNotifications, dismissNotification, notify, notifyError } from './store/notifications'

export { ThemeProvider } from './themes/context'

export type * from './types/deskagent'
