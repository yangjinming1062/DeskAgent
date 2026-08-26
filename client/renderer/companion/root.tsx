import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'

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
import { useInteractiveRegion, useWindowMouseCapture } from '@/companion/interactive-regions'
import { $renderMode, hydrateMesh2D } from '@/companion/mesh2d/mesh2d-store'
import { hydratePersona } from '@/companion/persona-store'
import { hydratePortrait, hydratePortraitHistory } from '@/companion/portrait-store'
import { $preferredCallMode } from '@/companion/prefs'
import { $puppetInfo, hydratePuppet } from '@/companion/puppet/puppet-store'
import { initSpatial } from '@/companion/spatial'
import { NotificationStack } from '@/shared'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { strings } from '@/shared/strings'

import { ActivationOverlay } from './activation/activation-overlay'
import { ChatDock } from './chat-dock'
import { DeveloperOverlay } from './developer-overlay'
import { EggStage } from './egg-stage'
import { handleCompanionEvent } from './events'
import { handlePokeInteraction } from './interaction'
import { MediaViewerOverlay } from './media-viewer-overlay'
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

// 3D / 2D 渲染管线：把这两个组件连同其 three + draco wasm + GLTF loader 全家桶
// 从启动关键路径挪走。Onboarding 期间 (showOnboarding 为 true) 本来就不挂载它们，
// 让 Vite 把 three.module.js + draco_decoder.wasm 等 25MB 模块拆成单独 chunk，
// 在 lifecycle=ready 后按需请求，避开启动尖峰把风扇拉满。
const Companion3D = lazy(() => import('./3d/companion-3d').then(m => ({ default: m.Companion3D })))
// puppet（PSD 链）沿用 WebGL/vite 懒加载策略：PSD 装配 + vendor
// rigger/ag-psd 都不在启动关键路径上（Phase 6）。
const PuppetStage = lazy(() => import('./puppet/PuppetStage').then(m => ({ default: m.PuppetStage })))

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
  // toast 的关闭/展开按钮需要真实可点——透明窗口把它的矩形注册进交互区域。
  const notificationStackRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('notification-stack', notificationStackRef)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const lifecycle = useStore($companionLifecycle)
  const chatOpen = useStore($chatOpen)
  const renderMode = useStore($renderMode)
  const puppet = useStore($puppetInfo)
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
  // 通过全局 window.__spiritagentOpenDock 暴露，sprite-stage 等深层组件也能复用同一不变量。
  const openDock = useCallback((kind: 'chat' | 'memory' | 'settings' | 'voice'): void => {
    setChatOpen(kind === 'chat')
    setVoiceCallOpen(kind === 'voice')
    setSettingsOpen(kind === 'settings')
    setMemoryOpen(kind === 'memory')
  }, [])

  useEffect(() => {
    const w = window as unknown as { __spiritagentOpenDock?: typeof openDock }
    w.__spiritagentOpenDock = openDock

    return () => {
      delete (window as unknown as { __spiritagentOpenDock?: typeof openDock }).__spiritagentOpenDock
    }
  }, [openDock])

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

  // 登出 / 反激活时释放 3D 模板缓存的 GPU 资源。GLB 缓存归伙伴层自管
  // （shared → companion 是禁用方向，auth store 不反向依赖渲染层）；动态导入
  // 避免把 three 拉进启动 chunk，且只在确有模型资产时才值得加载该模块。
  useEffect(
    () =>
      $auth.listen(state => {
        if (state.kind === 'unauthenticated' && $modelInfo.get().asset_url) {
          void import('./3d/gltf-instance-cache').then(m => m.clearAllGltf())
        }
      }),
    []
  )

  // 托盘「激活...」入口的对偶：主进程只调 showMainWindow() 不够——
  // 激活浮层是 React state，关掉之后必须显式翻回来，否则就是死锁。
  useEffect(() => {
    const off = window.spiritagent.onTrayActivate?.(() => setActivationOpen(true))

    return () => off?.()
  }, [])

  // 托盘「打开对话」（DESIGN §6.1 对话模式触发源）：聊天面板同样要走
  // openDock 的 dock 互斥，不能直接 setChatOpen。
  useEffect(() => {
    const off = window.spiritagent.onTrayOpenChat?.(() => {
      if (auth.kind === 'authenticated') {
        openDock('chat')
      }
    })

    return () => off?.()
  }, [auth.kind, openDock])

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
  // 4) 若 state?.complete 不是 true，则 lifecycle='onboarding'，但 onboardingOpen 保持 false：
  //    让桌面蛋先常驻（DESIGN §4「蛋破碎后开始对话」），由用户戳击蛋才进入向导。
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
      // onboardingOpen 仅在用户主动戳击蛋 / 重新进入向导时才打开；
      // 保持 false 让 eggVisible 走通桌面蛋分支。
      setOnboardingOpen(false)

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
  const eggVisible = authed && lifecycle === 'onboarding' && !onboardingOpen

  // 渲染级联编排器（DESIGN §1.2「永不空白」）——嵌在组件体内，因依赖多个组件内 useStore 变量；
  // 抽到 useMemo 避免每次渲染重建闭包。2D = PSD 链（puppet，Phase 6）；puppet 装配失败
  // 写 error 熄灭 $puppetReady 后落 3D（CharacterController 内部有程序化蛋兜底）。
  const renderLayer = useMemo<'puppet' | 'companion3d'>(() => {
    const puppetReady = Boolean(puppet.psdUrl) && !puppet.error
    const modelFailed = glbLoadFailed || modelInfo.status === 'failed'
    const modelReady = renderMode === '3d' && !modelFailed && modelInfo.status === 'succeeded'

    if (renderMode === '2d' && puppetReady) {
      return 'puppet'
    }

    if (modelReady) {
      return 'companion3d'
    }

    // 3D 偏好但失败 / 尚未就绪时，2D 已就绪就降级到 2D
    if (puppetReady) {
      return 'puppet'
    }

    // 双方都未就绪：选 3D 路径，CharacterController 内部会走程序化蛋兜底
    return 'companion3d'
  }, [renderMode, puppet.psdUrl, puppet.error, glbLoadFailed, modelInfo.status])

  useEffect(() => {
    if (lifecycle !== 'ready') {
      return
    }

    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()
    void Promise.all([
      hydratePersona(),
      hydrateModel(),
      hydrateExpressions(),
      // puppet 分流依赖 mesh2d 行的 manifest 内容，串在其后
      hydrateMesh2D().then(() => hydratePuppet())
    ])

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
  }, [lifecycle, gatewayState, requestGateway, openDock])

  const onTap = (nx?: number, ny?: number) => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      // 2D 路径下尝试子区域命中；3D 路径忽略 nx/ny（silhouette hit 走自己的通道）
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

    // DESIGN §6.1：用户偏好主交互模式为语音通话时，进入桌面后自动打开通话面板。
    if ($preferredCallMode.get() === 'voice') {
      setVoiceCallOpen(true)
    }
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
        {eggVisible ? (
          <EggStage onTap={() => setOnboardingOpen(true)} />
        ) : showOnboarding ? null : (
          <Suspense fallback={null}>{renderLayer === 'puppet' ? <PuppetStage /> : <Companion3D />}</Suspense>
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
      {authed && <MediaViewerOverlay />}
      <NotificationStack regionRef={notificationStackRef} />
      <BootFailureOverlay />
      <DeveloperOverlay />
      {authed && <GatewayBooter />}
    </>
  )
}
