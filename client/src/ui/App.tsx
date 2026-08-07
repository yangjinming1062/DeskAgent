import { useEffect, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import { $authState, $authToken, $baseUrl, getApiClient } from '../state/auth-store'
import { gateway, initGatewayStateSync } from '../state/gateway-store'
import { createEventHandler } from '../state/events'
import { $spriteState, setSpriteState } from '../state/companion-store'
import { $chatOpen } from '../state/chat-store'
import { $wardrobeOpen, refreshEquippedAndApply } from '../state/wardrobe-store'
import { CompanionCanvas } from './CompanionCanvas'
import { LoginScreen } from './LoginScreen'
import { ChatDock } from './ChatDock'
import { WardrobePanel } from './WardrobePanel'

export function App(): React.JSX.Element {
  const authState = useStore($authState)
  const chatOpen = useStore($chatOpen)
  const [modelUrl, setModelUrl] = useState<string | null>(null)
  const gatewaySetup = useRef(false)

  // Verify stored token on mount
  useEffect(() => {
    if (authState !== 'loading') return
    const token = $authToken.get()
    if (!token) { $authState.set('unauthenticated'); return }
    const api = getApiClient()
    api.getPersona().then(() => $authState.set('authenticated')).catch(() => $authState.set('unauthenticated'))
  }, [authState])

  // Gateway connection + event handler when authenticated
  useEffect(() => {
    if (authState !== 'authenticated' || gatewaySetup.current) return
    gatewaySetup.current = true
    initGatewayStateSync()

    const base = $baseUrl.get()
    const api = getApiClient()

    api.getWsTicket().then(ticket =>
      gateway.connect(`${base.replace(/^http/, 'ws')}/api/chat/ws?ticket=${ticket.access_token}`)
    ).catch(err => {
      console.error('Gateway connection failed:', err)
      setSpriteState('disconnected')
    })

    const unsubEvents = gateway.onEvent(createEventHandler({
      onModelReady: url => setModelUrl(url),
      onWardrobeUpdate: () => { void refreshEquippedAndApply(api) }
    }))

    api.getModel().then(m => { if (m?.asset_url) setModelUrl(m.asset_url) }).catch(err => {
      console.error('Initial model load failed:', err)
    })

    let disconnectTimer: ReturnType<typeof setTimeout> | null = null
    const unsubGw = gateway.onState(s => {
      if (s === 'open') {
        if (disconnectTimer) { clearTimeout(disconnectTimer); disconnectTimer = null }
        const cur = $spriteState.get()
        if (cur === 'disconnected' || cur === 'sleeping') setSpriteState('idle', { force: true })
      } else if (s === 'closed' || s === 'error') {
        if (disconnectTimer) clearTimeout(disconnectTimer)
        disconnectTimer = setTimeout(() => {
          disconnectTimer = null
          if (gateway.state !== 'open') setSpriteState('disconnected')
        }, 3000)
      }
    })

    return () => {
      unsubEvents()
      unsubGw()
      if (disconnectTimer) clearTimeout(disconnectTimer)
      gateway.close()
      gatewaySetup.current = false
    }
  }, [authState])

  if (authState === 'loading') return <div className="loading-screen">连接中…</div>
  if (authState !== 'authenticated') return <LoginScreen />
  return (
    <>
      <CompanionCanvas modelUrl={modelUrl} />
      {chatOpen && <ChatDock />}
      <WardrobePanel />
      <div id="hud">
        <div id="instructions">点击角色对话 · 拖拽移动</div>
        <button id="wardrobe-btn" onClick={() => $wardrobeOpen.set(!$wardrobeOpen.get())}>衣橱</button>
      </div>
    </>
  )
}
