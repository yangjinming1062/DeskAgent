import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { clearClipCatalog } from '@/companion/clip-store'
import { $disturbanceTier, type DisturbanceTier, setDisturbanceTier } from '@/companion/companion-store'
import { $persona } from '@/companion/persona-store'
import { $companionVoiceId, $responseMode, type ResponseMode, setCompanionVoiceId, setResponseMode } from '@/companion/prefs'
import { speak } from '@/companion/tts'
import { fetchVoiceCatalog, sampleLine, type VoiceOption } from '@/companion/voice'

import { PersonaSection } from './persona-editor'

interface SettingsOverlayProps {
  onClose: () => void
}

const TIERS: { id: DisturbanceTier; label: string; hint: string }[] = [
  { id: 'proactive', label: '积极主动', hint: '语音、气泡、主动消息全开放' },
  { id: 'normal', label: '常规', hint: '仅轻量文字消息' },
  { id: 'quiet', label: '保持安静', hint: '不发消息，但情绪仍流动' }
]

// Companion-specific settings live in the sprite window (where the WS gateway
// boots) rather than the framed tool window, because voice/clip/avatar calls
// are JSON-RPC over the gateway. General app settings stay in the tray tool
// window. Covers plan §6 companion items: voice, avatar, response mode, tier.
export function CompanionSettings({ onClose }: SettingsOverlayProps) {
  const tier = useStore($disturbanceTier)
  const responseMode = useStore($responseMode)
  const currentVoice = useStore($companionVoiceId)
  const persona = useStore($persona)
  const { requestGateway } = useGatewayRequest()
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [regenerating, setRegenerating] = useState(false)
  const [avatarHint, setAvatarHint] = useState<string | null>(null)

  useEffect(() => {
    void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: false })
    void window.deskagent.sprite.setAlwaysOnTop({ on: false })
    void fetchVoiceCatalog(requestGateway).then(setVoices)

    return () => {
      void window.deskagent.sprite.setAlwaysOnTop({ on: true })
      void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
    }
  }, [requestGateway])

  const regenerate = async () => {
    setRegenerating(true)
    setAvatarHint(null)
    clearClipCatalog()

    try {
      const res = await requestGateway<{ asset_url?: string }>('avatar.regenerate', {})
      setAvatarHint(res?.asset_url ? '换好啦，新形象已生成～' : '暂时换不出来，稍后再试')
    } catch {
      setAvatarHint('暂时换不出来，稍后再试')
    } finally {
      setRegenerating(false)
    }
  }

  const upload = async () => {
    try {
      const [path] = await window.deskagent.selectPaths({ title: '选择一张图片作为形象', filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }] })

      if (!path) {return}
      const dataUrl = await window.deskagent.readFileDataUrl(path)
      const comma = dataUrl.indexOf(',')
      const mime = comma > 0 ? dataUrl.slice(5, comma) : 'image/png'
      const base64 = comma > 0 ? dataUrl.slice(comma + 1) : ''

      if (!base64) {return}
      setRegenerating(true)

      const res = await window.deskagent.api<{ asset_url?: string }>({
        path: '/api/companion/avatar/upload',
        method: 'POST',
        body: { image: base64, content_type: mime }
      })

      clearClipCatalog()
      setAvatarHint(res?.asset_url ? '上传成功～' : '上传失败了')
    } catch {
      setAvatarHint('上传失败了，换张图试试？')
    } finally {
      setRegenerating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center px-6 pb-10" style={{ pointerEvents: 'auto' }}>
      <div className="flex h-[min(70vh,600px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/60 text-white shadow-2xl backdrop-blur-md">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h2 className="text-sm font-semibold">伙伴设置</h2>
          <button aria-label="关闭" className="text-white/50 transition hover:text-white" onClick={onClose} type="button">✕</button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
          <PersonaSection />

          {/* Response mode */}
          <Section hint="语音通话模式始终语音，不受此设置影响" title="对话回应方式">
            <div className="flex gap-2">
              {(['text', 'voice'] as ResponseMode[]).map(m => (
                <button
                  className={`flex-1 rounded-lg border px-3 py-2 text-xs transition ${responseMode === m ? 'border-white/60 bg-white/15 font-medium' : 'border-white/15 bg-white/5 text-white/70 hover:bg-white/10'}`}
                  key={m}
                  onClick={() => setResponseMode(m)}
                  type="button"
                >
                  {m === 'text' ? '默认文字' : '始终语音'}
                </button>
              ))}
            </div>
          </Section>

          {/* Disturbance tier */}
          <Section hint="只约束伙伴的主动行为，你发起的交互不受限" title="打扰档位">
            <div className="space-y-1.5">
              {TIERS.map(t => (
                <button
                  className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-xs transition ${tier === t.id ? 'border-white/60 bg-white/15' : 'border-white/10 bg-white/5 hover:bg-white/10'}`}
                  key={t.id}
                  onClick={() => {
                    setDisturbanceTier(t.id)
                    void requestGateway('companion.set_disturbance_tier', { tier: t.id }).catch(() => {})
                  }}
                  type="button"
                >
                  <span className="font-medium">{t.label}</span>
                  <span className="text-white/40">{t.hint}</span>
                </button>
              ))}
            </div>
          </Section>

          {/* Voice */}
          <Section hint="选择伙伴的说话音色" title="音色">
            {voices.length === 0 ? (
              <p className="text-xs text-white/40">未配置 TTS 引擎，使用默认音色。</p>
            ) : (
              <div className="space-y-1.5">
                {voices.map(v => (
                  <div
                    className={`flex items-center justify-between rounded-lg border px-3 py-2 text-xs transition ${currentVoice === v.id ? 'border-white/60 bg-white/15' : 'border-white/10 bg-white/5'}`}
                    key={v.id}
                  >
                    <div>
                      <p className="font-medium">{v.label}</p>
                      <p className="text-white/40">{v.tags.join(' · ')}</p>
                    </div>
                    <div className="flex gap-2">
                      <button className="text-white/60 transition hover:text-white" onClick={() => void speak(sampleLine(persona?.name ?? ''), v.id || undefined)} type="button">试听</button>
                      <button
                        className={`transition ${currentVoice === v.id ? 'text-emerald-400' : 'text-white/60 hover:text-white'}`}
                        onClick={() => setCompanionVoiceId(v.id)}
                        type="button"
                      >
                        {currentVoice === v.id ? '✓ 使用中' : '使用'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* Avatar */}
          <Section hint="重新生成或上传自定义形象；衍生动画会重新生成" title="形象">
            <div className="flex gap-2">
              <button
                className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/80 transition hover:bg-white/15 disabled:opacity-40"
                disabled={regenerating}
                onClick={regenerate}
                type="button"
              >
                {regenerating ? '生成中…' : '重新生成'}
              </button>
              <button
                className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/80 transition hover:bg-white/15 disabled:opacity-40"
                disabled={regenerating}
                onClick={upload}
                type="button"
              >
                上传图片
              </button>
            </div>
            {avatarHint && <p className="mt-2 text-xs text-amber-300/80">{avatarHint}</p>}
          </Section>
        </div>
      </div>
    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-white/80">{title}</p>
      {children}
      {hint && <p className="mt-1.5 text-[10px] text-white/30">{hint}</p>}
    </div>
  )
}
