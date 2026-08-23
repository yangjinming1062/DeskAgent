import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $glbLoadFailed, $modelInfo, hydrateExpressions, hydrateModel } from '@/companion/3d/model-store'
import { startActivityMonitor } from '@/companion/activity'
import { BootFailureOverlay } from '@/companion/boot/boot-failure-overlay'
import { useGatewayBoot } from '@/companion/boot/use-gateway-boot'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $chatOpen, setChatOpen } from '@/companion/chat-store'
import {
  $companionLifecycle,
  $voiceCallOpen,
  reportUserActivity,
  setCompanionLifecycle
} from '@/companion/companion-store'
import { useWindowMouseCapture } from '@/companion/interactive-regions'
import { $mesh2dInfo, $renderMode, hydrateMesh2D } from '@/companion/mesh2d/mesh2d-store'
import { Mesh2DCanvas } from '@/companion/mesh2d/Mesh2DCanvas'
import { hydratePersona } from '@/companion/persona-store'
import { hydratePortrait, hydratePortraitHistory } from '@/companion/portrait-store'
import { initSpatial } from '@/companion/spatial'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { strings } from '@/shared/strings'

import { Companion3D } from './3d/companion-3d'
import { ActivationOverlay } from './activation/activation-overlay'
import { ChatDock } from './chat-dock'
import { DeveloperOverlay } from './developer-overlay'
import { handleCompanionEvent } from './events'
import { handlePokeInteraction } from './interaction'
import { MemoryBrowser } from './memory-browser'
import { $mesh2dHitmap } from './mesh2d/mesh2d-store'
import { OnboardingFlow } from './onboarding/onboarding-flow'
import { speakProactive } from './proactive/proactive'
import { ProactiveBubble } from './proactive/proactive-bubble'
import { CompanionSettings } from './settings-overlay'
import { SpriteContextMenu } from './sprite/context-menu'
import { $contextMenuPos } from './sprite/context-menu-store'
import { SpriteStage } from './sprite/sprite-stage'
import { VoiceCallDock } from './voice-call-dock'
import { checkCompanionVoiceValidity } from './voice-validity'

// 把 gateway 启动挂在 mount effect 里——这样只在已鉴权时才会跑。
// 当 $auth 切回未鉴权（登出 / 过期）时这里会卸载，useGatewayBoot 的 cleanup
// 负责拆掉 WS。handleGatewayEvent 把流式聊天帧分派到 chat store + 状态机（events.ts）。
function GatewayBooter(): null {
  useGatewayBoot({
    handleGatewayEvent: handleCompanionEvent,
    onConnectionReady: () => {},
    onGatewayReady: () => {}
  })

  return null
}

export function CompanionRoot(): React.JSX.Element {
  useWindowMouseCapture()
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const lifecycle = useStore($companionLifecycle)
  const chatOpen = useStore($chatOpen)
  const renderMode = useStore($renderMode)
  const mesh2d = useStore($mesh2dInfo)
  const modelInfo = useStore($modelInfo)
  const glbLoadFailed = useStore($glbLoadFailed)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [activationOpen, setActivationOpen] = useState(false)
  const [voiceCallOpen, setVoiceCallOpen] = useState(false)
  useEffect(() => {
    $voiceCallOpen.set(voiceCallOpen)
  }, [voiceCallOpen])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const { requestGateway } = useGatewayRequest()

  // 精灵窗口的 dock 互斥——打开一个就关掉其他，避免弹层堆叠。
  const openDock = (kind: 'chat' | 'memory' | 'settings' | 'voice'): void => {
    setChatOpen(kind === 'chat')
    setVoiceCallOpen(kind === 'voice')
    setSettingsOpen(kind === 'settings')
    setMemoryOpen(kind === 'memory')
  }

  const validityCheckedRef = useRef(false)
  // 标记一次点击精灵后触发了登录流程的「点击」——用于区分全新登录
  // 与带缓存会话的启动（后者需要再点一下才会打开向导）。
  const pendingOnboardingAutoOpenRef = useRef(false)

  useEffect(() => {
    void hydrateAuth()
  }, [])

  useEffect(() => initSpatial(), [])

  // 挂载时一次性水合 runner-status atom——与 hydrateAuth 同款模式，
  // 让伙伴侧消费者（activity.ts 等）能直接读 $runnerPhase，
  // 不必各自再实现 subscribe + 同步 getter 的组合。
  useEffect(() => {
    void hydrateRunnerStatus()
  }, [])

  useEffect(() => {
    const off = window.spiritagent.onAuthChanged(payload => applyAuthBroadcast(payload))

    return () => off()
  }, [])

  useEffect(() => {
    const off = window.spiritagent.onSessionExpired(() => void logout())

    return () => off()
  }, [])

  // 托盘菜单的「登出」入口会触发这个桥；主进程侧登出也会在下一次会话检查时
  // 触发 `onSessionExpired`，但显式路由能让用户在点托盘项时 UI 更跟手。
  useEffect(() => {
    const off = window.spiritagent.onTrayLogout?.(() => void logout())

    return () => off?.()
  }, [])

  // 托盘「激活...」入口的对偶：主进程只调 showMainWindow() 不够——
  // 激活浮层是 React state，关掉之后必须显式翻回来，否则就是死锁。
  useEffect(() => {
    const off = window.spiritagent.onTrayActivate?.(() => setActivationOpen(true))

    return () => off?.()
  }, [])

  // 未鉴权时自动开激活浮层：首次 hydrateAuth 完成（pending → unauthenticated）、
  // 以及反激活之后。原先只能戳精灵触发，但未鉴权时精灵实体本身不可见、戳不到，
  // 这条链路在未激活用户那里是断的。
  useEffect(() => {
    if (auth.kind === 'unauthenticated') {
      setActivationOpen(true)
    }
  }, [auth.kind])

  // 登出时清掉，保证下次登录从干净状态开始。
  useEffect(() => {
    if (auth.kind === 'unauthenticated') {
      pendingOnboardingAutoOpenRef.current = false
    }
  }, [auth.kind])

  // 仅开发期：注入一条测试主动消息（Ctrl+Shift+P）来跑通
  // companion.message 接收 + 气泡 + TTS 全链路，但不走 Backend 的 send_message 路径。
  // 生产构建里会被剔除。
  useEffect(() => {
    if (import.meta.env.PROD) {
      return
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault()
        void speakProactive('（测试）嘿，休息一下眼睛吧～')
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 鉴权后查询 onboarding 状态。
  // 1) 先用 GET /api/companion/onboarding/state（REST）做权威即时检查。
  // 2) 不可用时回退到 requestGateway('onboarding.get_state')。
  // 3) 仅在 state?.complete === true 时把 lifecycle 设为 'ready'。
  //    不要回退到 persona.is_complete（角色题保存后它会在 onboarding 中途变成 true）。
  // 4) 若 state?.complete 不是 true，则 lifecycle='onboarding' 并 setOnboardingOpen(true)。
  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      setCompanionLifecycle('unauthed')
      setOnboardingOpen(false)

      return
    }

    let cancelled = false

    const checkState = async () => {
      let state: { complete?: boolean } | null = null

      try {
        state = await window.spiritagent.api<{ complete?: boolean }>({
          path: '/api/companion/onboarding/state'
        })
      } catch {
        state = await requestGateway<{ complete?: boolean }>('onboarding.get_state', {}).catch(() => null)
      }

      if (cancelled) {
        return
      }

      const onboardingDone = state?.complete === true
      setCompanionLifecycle(onboardingDone ? 'ready' : 'onboarding')
      setOnboardingOpen(!onboardingDone)

      if (onboardingDone) {
        void hydratePortrait()
        void hydratePortraitHistory()
      }
    }

    void checkState()

    return () => {
      cancelled = true
    }
  }, [auth.kind, requestGateway])

  const authed = auth.kind === 'authenticated'
  const showOnboarding = authed && lifecycle === 'onboarding' && onboardingOpen

  useEffect(() => {
    if (lifecycle !== 'ready') {
      return
    }

    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()
    void Promise.all([hydratePersona(), hydrateModel(), hydrateExpressions(), hydrateMesh2D()])

    return () => {
      window.removeEventListener('keydown', onKey)
      stopActivity()
    }
  }, [lifecycle])

  // 检测云端目录里已经下架的伙伴 voice id（供应商裁剪 / 改名，或换了供应商）。
  // 后端对未知 id 是宽容的，这里只是一次性提示，不是硬错误。
  useEffect(() => {
    if (lifecycle !== 'ready' || gatewayState !== 'open' || validityCheckedRef.current) {
      return
    }

    validityCheckedRef.current = true

    void checkCompanionVoiceValidity(requestGateway).then(result => {
      if (result.valid) {
        return
      }

      notify({
        kind: 'warning',
        title: strings.notifications.voice.invalidTitle,
        message: strings.notifications.voice.invalidMessage(result.name),
        action: { label: strings.notifications.voice.invalidAction, onClick: () => openDock('settings') }
      })
    })
  }, [lifecycle, gatewayState, requestGateway])

  const onTap = (nx?: number, ny?: number) => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      // mesh2d 路径下尝试子区域命中；3D 路径忽略 nx/ny（silhouette hit 走自己的通道）
      let region: string | undefined

      if (nx !== undefined && ny !== undefined) {
        const hit = $mesh2dHitmap.get()
        const result = hit ? hit.hit(nx, ny) : null
        region = result?.region
      }

      handlePokeInteraction(region)

      return
    }

    // 鉴权前：点击打开伙伴窗口内的激活浮层。
    pendingOnboardingAutoOpenRef.current = true
    setActivationOpen(true)
  }

  // Plan §4.3：双击 ready 状态的伙伴打开 Chat。
  const onDoubleTap = () => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      openDock('chat')

      return
    }

    setActivationOpen(true)
  }

  // onboarding 完成触发 3D 模型生成（base_texture 供应商是即时的——
  // 3D 生成触发在 confirm-front 成功回调里完成——onboarding 流程只负责"展示与完成"，不再触发 3D 任务。
  const onOnboardingComplete = () => {
    setOnboardingOpen(false)
    setCompanionLifecycle('ready')
  }

  return (
    <>
      {activationOpen && !authed && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        hidden={chatOpen || showOnboarding}
        onContextMenu={e => {
          $contextMenuPos.set({ x: e.clientX, y: e.clientY })
        }}
        onDoubleTap={onDoubleTap}
        onTap={onTap}
      >
        {showOnboarding ? null : renderMode === '2d' ||
          (renderMode === '3d' &&
            (glbLoadFailed || modelInfo.status === 'failed') &&
            mesh2d.status === 'succeeded' &&
            Boolean(mesh2d.manifestUrl)) ? (
          <Mesh2DCanvas />
        ) : (
          <Companion3D />
        )}
      </SpriteStage>
      <SpriteContextMenu
        onOpenActivation={() => setActivationOpen(true)}
        onOpenChat={() => openDock('chat')}
        onOpenMemory={() => openDock('memory')}
        onOpenSettings={() => openDock('settings')}
        onOpenVoiceCall={() => openDock('voice')}
      />
      {authed && chatOpen && <ChatDock onClose={() => setChatOpen(false)} onOpenVoiceCall={() => openDock('voice')} />}
      {authed && voiceCallOpen && <VoiceCallDock onClose={() => setVoiceCallOpen(false)} />}
      {authed && settingsOpen && <CompanionSettings onClose={() => setSettingsOpen(false)} />}
      {authed && memoryOpen && <MemoryBrowser onClose={() => setMemoryOpen(false)} />}
      {authed && <ProactiveBubble />}
      <BootFailureOverlay />
      <DeveloperOverlay />
      {authed && <GatewayBooter />}
    </>
  )
}
