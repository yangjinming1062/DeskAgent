import { lazy, Suspense, useEffect, useState } from 'react'

import { BootFailureOverlay } from '@/components/boot-failure-overlay'
import { BrandMark } from '@/components/brand-mark'
import { GatewayConnectingOverlay } from '@/components/gateway-connecting-overlay'
import { logout } from '@/store/auth'

import { useGatewayBoot } from './gateway/hooks/use-gateway-boot'
import { useGatewayRequest } from './gateway/hooks/use-gateway-request'

const SettingsView = lazy(async () => ({ default: (await import('./settings')).SettingsView }))

// Authenticated root. The hub layer (WS gateway, runner bridge, tool sync) boots
// here; the companion UI (sprite window + interaction surface) is built on top
// of this base in a later phase. Until then the window hosts the boot/connecting
// overlays and the settings overlay, opened from the tray context menu.
export function DesktopController() {
  const { connectionRef, gatewayRef } = useGatewayRequest()
  const [settingsOpen, setSettingsOpen] = useState(false)

  useGatewayBoot({
    handleGatewayEvent: () => {
      /* companion layer will dispatch WS events here */
    },
    onConnectionReady: connection => {
      connectionRef.current = connection
    },
    onGatewayReady: gateway => {
      gatewayRef.current = gateway
    }
  })

  useEffect(() => {
    const offOpen = window.deskagent?.onOpenSettings?.(() => setSettingsOpen(true))
    const offLogout = window.deskagent?.onTrayLogout?.(() => void logout())

    return () => {
      offOpen?.()
      offLogout?.()
    }
  }, [])

  return (
    <div className="fixed inset-0 flex flex-col items-center justify-center gap-6 bg-(--ui-chat-surface-background)">
      <GatewayConnectingOverlay />
      <BootFailureOverlay />
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsView gateway={gatewayRef.current} onClose={() => setSettingsOpen(false)} />
        </Suspense>
      )}
      {!settingsOpen && (
        <div className="flex flex-col items-center gap-3 text-center">
          <BrandMark className="size-12 text-(--theme-primary)" />
          <p className="text-sm text-muted-foreground">DeskAgent</p>
        </div>
      )}
    </div>
  )
}
