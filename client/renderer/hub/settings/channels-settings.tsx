import { IconBrandWechat } from '@tabler/icons-react'
import { QRCodeSVG } from 'qrcode.react'
import { useCallback, useEffect, useState } from 'react'

import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, ConfirmDialog, Spinner } from '@/shared/panel'
import {
  actOnChannelPeer,
  getWeixinLoginState,
  listChannelPeers,
  listChannels,
  logoutChannel,
  startWeixinLogin
} from '@/shared/spiritagent'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type {
  ChannelBindingInfo,
  ChannelLoginState,
  ChannelPeerAction,
  ChannelPeerInfo
} from '@/shared/types/spiritagent'

import {
  EmptyState,
  ListRow,
  LoadingState,
  Pill,
  SectionHeading,
  SettingsContent,
  SettingsSubsection
} from './primitives'

const WEIXIN_CHANNEL = 'weixin_ilink'
const LOGIN_POLL_INTERVAL_MS = 2000

function isDataImage(content: string): boolean {
  return /^data:image\//i.test(content) || /^(iVBORw0KGgo|\/9j\/|PHN2Zy)/.test(content)
}

function normalizeDataImage(content: string): string {
  if (/^data:image\//i.test(content)) {
    return content
  }

  if (/^iVBORw0KGgo/.test(content)) {
    return `data:image/png;base64,${content}`
  }

  if (/^\/9j\//.test(content)) {
    return `data:image/jpeg;base64,${content}`
  }

  if (/^PHN2Zy/.test(content)) {
    return `data:image/svg+xml;base64,${content}`
  }

  return content
}

export function ChannelsSettings(): React.JSX.Element {
  const t = strings.settings.channels

  const [isLoading, setIsLoading] = useState(true)
  const [weixinBinding, setWeixinBinding] = useState<ChannelBindingInfo | null>(null)
  const [login, setLogin] = useState<ChannelLoginState | null>(null)
  const [loginPolling, setLoginPolling] = useState(false)
  const [loginBusy, setLoginBusy] = useState(false)
  const [confirmLogout, setConfirmLogout] = useState(false)
  const [peers, setPeers] = useState<ChannelPeerInfo[]>([])
  const [peerBusy, setPeerBusy] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const channels = await listChannels()
    const weixin = channels.items.find(item => item.channel === WEIXIN_CHANNEL)?.binding ?? null
    setWeixinBinding(weixin)

    if (weixin) {
      try {
        setPeers((await listChannelPeers(WEIXIN_CHANNEL)).items)
      } catch {
        // 未登录成功时 peers 端点可能 404，静默清空。
        setPeers([])
      }
    } else {
      setPeers([])
    }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        await reload()
      } catch (error) {
        notifyError(error, t.loadFailed)
      } finally {
        setIsLoading(false)
      }
    })()
  }, [reload, t.loadFailed])

  useEffect(() => {
    if (!loginPolling) {
      return
    }

    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const state = await getWeixinLoginState()
          setLogin(state)

          if (state.state === 'confirmed') {
            setLoginPolling(false)
            notify({ kind: 'success', message: t.weixin.loginSuccess })
            await reload()
          } else if (state.state === 'expired' || state.state === 'error') {
            setLoginPolling(false)
          }
        } catch {
          // setInterval 下一拍重试。
        }
      })()
    }, LOGIN_POLL_INTERVAL_MS)

    return () => window.clearInterval(timer)
  }, [loginPolling, reload, t.weixin.loginSuccess])

  const beginLogin = async (): Promise<void> => {
    setLoginBusy(true)

    try {
      const state = await startWeixinLogin()
      setLogin(state)
      setLoginPolling(true)
    } catch (error) {
      notifyError(error, t.weixin.loginStartFailed)
    } finally {
      setLoginBusy(false)
    }
  }

  const doLogout = async (): Promise<void> => {
    try {
      await logoutChannel(WEIXIN_CHANNEL)
      notify({ kind: 'info', message: t.weixin.logoutSuccess })
      setLogin(null)
      await reload()
    } catch (error) {
      notifyError(error, t.weixin.logoutFailed)
    }
  }

  const actOnPeer = async (peerId: string, action: ChannelPeerAction): Promise<void> => {
    setPeerBusy(peerId)

    try {
      setPeers((await actOnChannelPeer(WEIXIN_CHANNEL, peerId, action)).items)
    } catch (error) {
      notifyError(error, t.peers.actionFailed)
    } finally {
      setPeerBusy(null)
    }
  }

  if (isLoading) {
    return <LoadingState label={t.heading} />
  }

  const weixinStatus = weixinBinding?.status ?? 'disabled'
  const weixinStatusLabel = t.statusLabels[weixinStatus] ?? weixinStatus
  const connected = weixinStatus === 'connected'
  // 已连接下再次扫码（重登录）也走同一面板：绑定状态要等确认后才翻转，不能拿 connected 判断。
  const loginFlowActive = login !== null && login.state !== 'confirmed'

  const statusPill = loginFlowActive
    ? t.statusLabels.login_pending
    : connected
      ? weixinStatusLabel
      : weixinStatus === 'login_required'
        ? t.statusLabels.login_required
        : null

  const qrImage = login?.qr_image ?? null

  return (
    <SettingsContent>
      <SectionHeading meta={t.intro} title={t.heading} />
      <div className="space-y-8">
        <SettingsSubsection intro={t.weixin.intro} title={t.weixin.title}>
          <ListRow
            action={
              connected ? (
                <button className={BTN_SUBTLE} onClick={() => setConfirmLogout(true)} type="button">
                  {t.weixin.logoutAction}
                </button>
              ) : (
                <button className={BTN_PRIMARY} disabled={loginBusy} onClick={() => void beginLogin()} type="button">
                  {loginBusy ? <Spinner /> : <IconBrandWechat className="size-4" />}
                  {login?.state === 'expired' || login?.state === 'error' ? t.weixin.retryAction : t.weixin.loginAction}
                </button>
              )
            }
            description={connected ? t.weixin.connectedAs(weixinBinding?.account_name ?? '') : t.weixin.intro}
            title={
              <span className="flex items-center gap-2">
                <IconBrandWechat className="size-4 text-emerald-400" />
                {t.weixin.title}
                {statusPill ? (
                  <Pill tone={connected && !loginFlowActive ? 'primary' : 'muted'}>{statusPill}</Pill>
                ) : null}
              </span>
            }
          />
          {loginFlowActive ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-white/10 bg-white/5 px-4 py-5">
              {login.state === 'wait' && qrImage ? (
                isDataImage(qrImage) ? (
                  <img
                    alt="微信登录二维码"
                    className="size-44 rounded-lg bg-white p-2 object-contain"
                    src={normalizeDataImage(qrImage)}
                  />
                ) : (
                  <div className="flex items-center justify-center rounded-lg bg-white p-2.5 shadow-sm">
                    <QRCodeSVG bgColor="#ffffff" fgColor="#000000" level="M" size={160} value={qrImage} />
                  </div>
                )
              ) : null}
              <div className="text-xs text-white/60">
                {login.state === 'wait' ? t.weixin.qrPrompt : null}
                {login.state === 'scaned' ? t.weixin.scanedPrompt : null}
                {login.state === 'expired' ? t.weixin.expiredPrompt : null}
                {login.state === 'error' ? `${login.error ?? t.weixin.loginStartFailed}` : null}
                {login.state === 'confirmed' ? t.weixin.loginSuccess : null}
                {login.state === 'login_required' ? t.statusLabels.login_required : null}
              </div>
              {login.state === 'scaned' ? <Spinner /> : null}
            </div>
          ) : null}
        </SettingsSubsection>

        <SettingsSubsection intro={t.peers.intro} title={t.peers.title}>
          {weixinBinding && peers.length > 0 ? (
            peers.map(peer => (
              <ListRow
                action={
                  <div className="flex items-center gap-2">
                    {peer.status === 'pending' ? (
                      <button
                        className={BTN_PRIMARY}
                        disabled={peerBusy === peer.peer_id}
                        onClick={() => void actOnPeer(peer.peer_id, 'approve')}
                        type="button"
                      >
                        {t.peers.approve}
                      </button>
                    ) : null}
                    {peer.status === 'allowed' ? (
                      <button
                        className={BTN_GHOST}
                        disabled={peerBusy === peer.peer_id}
                        onClick={() => void actOnPeer(peer.peer_id, 'block')}
                        type="button"
                      >
                        {t.peers.block}
                      </button>
                    ) : null}
                    {peer.status === 'blocked' ? (
                      <button
                        className={BTN_GHOST}
                        disabled={peerBusy === peer.peer_id}
                        onClick={() => void actOnPeer(peer.peer_id, 'approve')}
                        type="button"
                      >
                        {t.peers.approve}
                      </button>
                    ) : null}
                    <button
                      className={BTN_GHOST}
                      disabled={peerBusy === peer.peer_id}
                      onClick={() => void actOnPeer(peer.peer_id, 'delete')}
                      type="button"
                    >
                      {t.peers.remove}
                    </button>
                  </div>
                }
                description={peer.peer_id}
                key={peer.peer_id}
                title={
                  <span className="flex items-center gap-2">
                    {peer.peer_name || peer.peer_id}
                    <Pill tone={peer.status === 'allowed' ? 'primary' : 'muted'}>
                      {peer.status === 'pending'
                        ? t.peers.pendingLabel
                        : peer.status === 'allowed'
                          ? t.peers.allowedLabel
                          : t.peers.blockedLabel}
                    </Pill>
                  </span>
                }
              />
            ))
          ) : (
            <EmptyState description={t.peers.intro} title={t.peers.empty} />
          )}
        </SettingsSubsection>
      </div>

      <ConfirmDialog
        confirmLabel={t.weixin.logoutAction}
        description={t.weixin.logoutConfirmDescription}
        onConfirm={() => void doLogout()}
        onOpenChange={setConfirmLogout}
        open={confirmLogout}
        title={t.weixin.logoutConfirmTitle}
        variant="destructive"
      />
    </SettingsContent>
  )
}
