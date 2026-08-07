import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $wardrobe, refreshEquippedAndApply, setWardrobe, type WardrobeItem } from '@/companion/3d/model-store'
import { pickAvatarImage } from '@/companion/avatar-image'
import { awaitAvatarRegeneration } from '@/companion/avatar-regen-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $effectiveTier, $userPreferredTier, setDisturbanceTier } from '@/companion/companion-store'
import { DISTURBANCE_TIERS } from '@/companion/disturbance-tiers'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { PersonaRetune } from '@/companion/persona-retune'
import { $persona, hydratePersona } from '@/companion/persona-store'
import {
  $companionVoiceId,
  $responseMode,
  type ResponseMode,
  setCompanionVoiceId,
  setResponseMode
} from '@/companion/prefs'
import { $defaultScale, setDefaultScale } from '@/companion/spatial'
import { speak } from '@/companion/tts'
import {
  designVoice,
  fetchVoiceCatalog,
  GENDER_OPTIONS,
  LANGUAGE_LABELS,
  playDataUrl,
  sampleLine,
  type VoiceCatalog,
  type VoiceDesignPreview
} from '@/companion/voice'

import { pushDevLog } from './developer-overlay'
import { PersonaSection } from './persona-editor'

interface SettingsOverlayProps {
  onClose: () => void
}

const TIERS = DISTURBANCE_TIERS

// Companion-specific settings live in the sprite window (where the WS gateway
// boots) rather than the framed tool window, because voice/clip/avatar calls
// are JSON-RPC over the gateway. General app settings stay in the tray tool
// window. Covers plan §6 companion items: voice, avatar, response mode, tier.
export function CompanionSettings({ onClose }: SettingsOverlayProps): React.ReactElement {
  const tier = useStore($userPreferredTier)
  const responseMode = useStore($responseMode)
  const currentVoice = useStore($companionVoiceId)
  const persona = useStore($persona)
  const defaultScale = useStore($defaultScale)
  const { requestGateway } = useGatewayRequest()

  const [catalog, setCatalog] = useState<VoiceCatalog>({
    provider: '',
    voices: [],
    supportsVoiceDesign: false,
    voiceDesignGuide: ''
  })

  const [langFilter, setLangFilter] = useState('')
  const [genderFilter, setGenderFilter] = useState('')
  const [regenerating, setRegenerating] = useState(false)
  const [avatarHint, setAvatarHint] = useState<string | null>(null)
  const [retuneOpen, setRetuneOpen] = useState(false)
  const wardrobe = useStore($wardrobe)
  const [wardrobeBusy, setWardrobeBusy] = useState(false)
  const [wardrobeHint, setWardrobeHint] = useState<string | null>(null)
  const [modelBusy, setModelBusy] = useState(false)
  const [modelHint, setModelHint] = useState<string | null>(null)

  const [retuneInitial, setRetuneInitial] = useState<{
    name: string
    personality: string
    speaking_style: string
    biological_type: string
    gender: string
    appearance: string
    background: string
    user_call_name: string
    user_gender: string
    user_age_bucket: string
    user_hobbies: string
    user_freeform: string
  } | null>(null)

  // Hydrate the retune wizard's user_* step from the backend before
  // showing the modal. Without this, step 5 shows blank fields and the
  // review screen renders '—' for each, misrepresenting the saved state.
  const openRetune = async () => {
    setRetuneOpen(true)

    try {
      const profile = (await requestGateway<Record<string, string>>('companion.get_user_profile', {})) ?? {}

      setRetuneInitial({
        name: persona?.name ?? '',
        personality: persona?.personality ?? '',
        speaking_style: persona?.speakingStyle ?? '',
        biological_type: persona?.biological_type ?? '',
        gender: persona?.gender ?? '',
        appearance: persona?.appearance ?? '',
        background: persona?.background ?? '',
        user_call_name: profile.user_call_name ?? '',
        user_gender: profile.user_gender ?? '',
        user_age_bucket: profile.user_age_bucket ?? '',
        user_hobbies: profile.user_hobbies ?? '',
        user_freeform: profile.user_freeform ?? ''
      })
    } catch {
      // Backend offline — fall back to the empty wizard. user_* defaults
      // remain blank; the user can still edit.
      setRetuneInitial({
        name: persona?.name ?? '',
        personality: persona?.personality ?? '',
        speaking_style: persona?.speakingStyle ?? '',
        biological_type: persona?.biological_type ?? '',
        gender: persona?.gender ?? '',
        appearance: persona?.appearance ?? '',
        background: persona?.background ?? '',
        user_call_name: '',
        user_gender: '',
        user_age_bucket: '',
        user_hobbies: '',
        user_freeform: ''
      })
    }
  }

  const [showDesign, setShowDesign] = useState(false)
  const [designPrompt, setDesignPrompt] = useState('')
  const [designPreview, setDesignPreview] = useState<VoiceDesignPreview | null>(null)
  const [designing, setDesigning] = useState(false)
  const [designHint, setDesignHint] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('companion-settings', panelRef)

  const filteredVoices = useMemo(
    () =>
      catalog.voices.filter(
        v =>
          (!langFilter || v.language === langFilter || v.language === 'multi') &&
          (!genderFilter || v.gender === genderFilter)
      ),
    [catalog.voices, langFilter, genderFilter]
  )

  const langOptions = useMemo(() => {
    const langs = new Set(catalog.voices.map(v => v.language).filter(Boolean))

    return ['', ...Array.from(langs).sort()]
  }, [catalog.voices])

  useEffect(() => {
    void window.deskagent.sprite.setAlwaysOnTop({ on: false })
    void fetchVoiceCatalog(requestGateway).then(setCatalog)

    return () => {
      void window.deskagent.sprite.setAlwaysOnTop({ on: true })
    }
  }, [requestGateway])

  const regenerate = async () => {
    setRegenerating(true)
    setAvatarHint(null)

    try {
      const res = await requestGateway<{ asset_url?: string; queued?: boolean; job_id?: string }>(
        'avatar.regenerate',
        {}
      )

      if (res?.asset_url) {
        setAvatarHint('换好啦，新形象已生成～')
      } else if (res?.queued && res.job_id) {
        const result = await awaitAvatarRegeneration(res.job_id)

        if (result.asset_url) {
          setAvatarHint('换好啦，新形象已生成～')
        } else {
          setAvatarHint(result.error ?? '暂时换不出来，稍后再试')
        }
      } else {
        setAvatarHint('暂时换不出来，稍后再试')
      }
    } catch {
      setAvatarHint('暂时换不出来，稍后再试')
    } finally {
      setRegenerating(false)
    }
  }

  const upload = async () => {
    const picked = await pickAvatarImage('选择一张图片作为形象')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setAvatarHint(picked.error)

      return
    }

    setRegenerating(true)

    try {
      const res = await window.deskagent.api<{ asset_url?: string }>({
        path: '/api/companion/avatar/upload',
        method: 'POST',
        body: { image: picked.image.base64, content_type: picked.image.contentType }
      })

      setAvatarHint(res?.asset_url ? '上传成功～' : '上传失败了')
    } catch {
      setAvatarHint('上传失败了，换张图试试？')
    } finally {
      setRegenerating(false)
    }
  }

  const runDesign = async () => {
    const prompt = designPrompt.trim()

    if (!prompt) {
      return
    }

    setDesigning(true)
    setDesignHint(null)

    try {
      const result = await designVoice(requestGateway, prompt, sampleLine(persona?.name ?? ''))
      setDesignPreview(result)
      playDataUrl(result.trialAudioDataUrl)
    } catch {
      setDesignHint('生成失败，换个描述试试？')
    } finally {
      setDesigning(false)
    }
  }

  // Trigger a fresh 3D model generation — backend pushes `model.ready` over
  // the gateway; events.ts forwards into model-store which the engine
  // listens to.
  const generateModel = async () => {
    setModelBusy(true)
    setModelHint(null)

    try {
      await window.deskagent.api<{ id?: number; asset_url?: string; status?: string }>({
        path: '/api/companion/model',
        method: 'POST'
      })
      setModelHint('已生成，加载中…')
    } catch (err) {
      setModelHint(err instanceof Error ? err.message : '生成失败')
    } finally {
      setModelBusy(false)
    }
  }

  // Pull the full wardrobe catalog — called on settings open; the
  // wardrobe.updated event also refreshes on backend-side mutations.
  useEffect(() => {
    void window.deskagent
      .api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
      .then(items => setWardrobe(items ?? []))
      .catch(() => {})
  }, [])

  const generateWardrobe = async () => {
    setWardrobeBusy(true)
    setWardrobeHint(null)

    try {
      await window.deskagent.api<WardrobeItem>({
        path: '/api/companion/wardrobe',
        method: 'POST',
        body: { name: '新造型', description: 'AI 生成的自定义纹理' }
      })
      // Backend will push wardrobe.updated; the events.ts handler refreshes
      // the catalog. We optimistically re-pull here too in case the event
      // arrives before the user closes the panel.
      const items = await window.deskagent.api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
      setWardrobe(items ?? [])
      refreshEquippedAndApply()
      setWardrobeHint('生成好了')
    } catch (err) {
      setWardrobeHint(err instanceof Error ? err.message : '生成失败')
    } finally {
      setWardrobeBusy(false)
    }
  }

  const equipWardrobe = async (itemId: number) => {
    try {
      await window.deskagent.api<WardrobeItem>({
        path: '/api/companion/wardrobe/equip',
        method: 'PUT',
        body: { item_id: itemId }
      })
      const items = await window.deskagent.api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
      setWardrobe(items ?? [])
      refreshEquippedAndApply()
    } catch (err) {
      setWardrobeHint(err instanceof Error ? err.message : '装备失败')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center px-6 pb-10" style={{ pointerEvents: 'none' }}>
      <div
        className="flex h-[min(70vh,600px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/60 text-white shadow-2xl backdrop-blur-md"
        ref={panelRef}
        style={{ pointerEvents: 'auto' }}
      >
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <h2 className="text-sm font-semibold">伙伴设置</h2>
          <button
            aria-label="关闭"
            className="text-white/50 transition hover:text-white"
            onClick={onClose}
            type="button"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
          <PersonaSection />

          {persona?.name && (
            <div className="-mt-3">
              <button
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-[11px] text-white/70 transition hover:bg-white/15"
                onClick={() => void openRetune()}
                type="button"
              >
                重新对话微调性格
              </button>
              <p className="mt-1 text-[10px] text-white/30">以对话方式分步调整（不会清除现有长期记忆）</p>
            </div>
          )}

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
                    const previous = $userPreferredTier.get()
                    setDisturbanceTier(t.id)
                    // Push the EFFECTIVE tier (incorporates the activity
                    // override) so the backend gate stays consistent with the
                    // renderer's view. Without this, an immersive focus
                    // context would un-mute the backend for the full poll-cycle
                    // window after a manual click.
                    const effectiveNow = $effectiveTier.get()
                    // Roll back locally if the backend rejects the tier.
                    requestGateway('companion.set_disturbance_tier', { tier: effectiveNow }).catch(err => {
                      setDisturbanceTier(previous)
                      pushDevLog(
                        'disturbance_tier_rejected',
                        JSON.stringify({ requested: t.id, previous, error: String(err?.message || err) })
                      )
                    })
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
          <Section hint="选择伙伴的说话音色，或设计一个专属音色" title="音色">
            {catalog.voices.length === 0 ? (
              <p className="text-xs text-white/40">未配置 TTS 引擎，使用默认音色。</p>
            ) : (
              <>
                {/* 语言 & 性别筛选 */}
                <div className="mb-2 flex flex-wrap items-center gap-1.5">
                  {langOptions.map(lang => (
                    <button
                      className={`rounded-full px-2.5 py-0.5 text-[10px] transition ${langFilter === lang ? 'bg-white/20 font-medium text-white' : 'bg-white/5 text-white/50 hover:bg-white/10'}`}
                      key={lang}
                      onClick={() => setLangFilter(lang)}
                      type="button"
                    >
                      {lang ? (LANGUAGE_LABELS[lang] ?? lang) : '全部'}
                    </button>
                  ))}
                  <span className="mx-1 text-white/20">|</span>
                  {GENDER_OPTIONS.map(g => (
                    <button
                      className={`rounded-full px-2.5 py-0.5 text-[10px] transition ${genderFilter === g.id ? 'bg-white/20 font-medium text-white' : 'bg-white/5 text-white/50 hover:bg-white/10'}`}
                      key={g.id}
                      onClick={() => setGenderFilter(g.id)}
                      type="button"
                    >
                      {g.label}
                    </button>
                  ))}
                </div>

                <div className="space-y-1.5">
                  {filteredVoices.map(v => (
                    <div
                      className={`flex items-center justify-between rounded-lg border px-3 py-2 text-xs transition ${currentVoice === v.id ? 'border-white/60 bg-white/15' : 'border-white/10 bg-white/5'}`}
                      key={v.id}
                    >
                      <div>
                        <p className="font-medium">{v.label}</p>
                        <p className="text-white/40">{v.tags.join(' · ')}</p>
                      </div>
                      <div className="flex gap-2">
                        <button
                          className="text-white/60 transition hover:text-white"
                          onClick={() => void speak(sampleLine(persona?.name ?? ''), v.id || undefined)}
                          type="button"
                        >
                          试听
                        </button>
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
                  {filteredVoices.length === 0 && <p className="text-xs text-white/30">当前筛选无匹配音色。</p>}
                </div>

                {/* 语音设计 */}
                {catalog.supportsVoiceDesign && (
                  <div className="mt-3">
                    <button
                      className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white/70 transition hover:bg-white/10"
                      onClick={() => setShowDesign(s => !s)}
                      type="button"
                    >
                      {showDesign ? '收起音色设计' : '设计专属音色 ✨'}
                    </button>
                    {showDesign && (
                      <div className="mt-2 rounded-lg border border-white/10 bg-white/5 p-3">
                        {catalog.voiceDesignGuide && (
                          <p className="whitespace-pre-line text-[10px] leading-relaxed text-white/40">
                            {catalog.voiceDesignGuide}
                          </p>
                        )}
                        <textarea
                          className="mt-2 w-full rounded border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white placeholder:text-white/30 focus:border-white/30 focus:outline-none"
                          onChange={e => setDesignPrompt(e.target.value)}
                          placeholder="描述你想要的音色…"
                          rows={3}
                          value={designPrompt}
                        />
                        <div className="mt-2 flex items-center gap-2">
                          <button
                            className="rounded-lg bg-white/10 px-3 py-1 text-xs text-white/80 transition hover:bg-white/20 disabled:opacity-40"
                            disabled={designing || !designPrompt.trim()}
                            onClick={() => void runDesign()}
                            type="button"
                          >
                            {designing ? '生成中…' : '生成预览'}
                          </button>
                          {designPreview && (
                            <>
                              <button
                                className="text-white/60 transition hover:text-white"
                                onClick={() => playDataUrl(designPreview.trialAudioDataUrl)}
                                type="button"
                              >
                                试听
                              </button>
                              <button
                                className={`transition ${currentVoice === designPreview.voiceId ? 'text-emerald-400' : 'text-white/60 hover:text-white'}`}
                                onClick={() => setCompanionVoiceId(designPreview.voiceId)}
                                type="button"
                              >
                                {currentVoice === designPreview.voiceId ? '✓ 使用中' : '使用'}
                              </button>
                            </>
                          )}
                        </div>
                        {designHint && <p className="mt-2 text-xs text-amber-300/80">{designHint}</p>}
                      </div>
                    )}
                  </div>
                )}
              </>
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

          {/* 3D model + wardrobe */}
          <Section hint="3D 模型 + 自定义换装纹理" title="3D 形象">
            <div className="flex gap-2">
              <button
                className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/80 transition hover:bg-white/15 disabled:opacity-40"
                disabled={modelBusy}
                onClick={generateModel}
                type="button"
              >
                {modelBusy ? '生成中…' : '重新生成模型'}
              </button>
              <button
                className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/80 transition hover:bg-white/15 disabled:opacity-40"
                disabled={wardrobeBusy}
                onClick={generateWardrobe}
                type="button"
              >
                {wardrobeBusy ? '生成中…' : '新造型'}
              </button>
            </div>
            {modelHint && <p className="mt-2 text-xs text-amber-300/80">{modelHint}</p>}
            {wardrobe.length > 0 && (
              <div className="mt-3 grid grid-cols-3 gap-2">
                {wardrobe.map(item => (
                  <button
                    className={`flex flex-col items-center gap-1 rounded-lg border p-2 text-[10px] transition ${
                      item.equipped
                        ? 'border-white/60 bg-white/15 text-white'
                        : 'border-white/15 bg-white/5 text-white/70 hover:bg-white/10'
                    }`}
                    key={item.id}
                    onClick={() => void equipWardrobe(item.id)}
                    type="button"
                  >
                    {item.texture_url ? (
                      <img alt={item.name} className="h-12 w-12 rounded object-cover" src={item.texture_url} />
                    ) : (
                      <span className="grid h-12 w-12 place-items-center rounded bg-white/10 text-base">
                        {item.category?.[0] ?? '?'}
                      </span>
                    )}
                    <span className="truncate">{item.name}</span>
                    {item.equipped && <span className="text-[9px] text-emerald-300">已装备</span>}
                  </button>
                ))}
              </div>
            )}
            {wardrobeHint && <p className="mt-2 text-xs text-amber-300/80">{wardrobeHint}</p>}
          </Section>

          <Section hint="精灵在桌面上的默认显示比例" title="形象大小">
            <div className="flex gap-2">
              {[0.5, 0.75, 1, 1.5, 2].map(s => (
                <button
                  className={`flex-1 rounded-lg border px-2 py-2 text-xs transition ${Math.abs(defaultScale - s) < 0.05 ? 'border-white/60 bg-white/15 font-medium' : 'border-white/15 bg-white/5 text-white/70 hover:bg-white/10'}`}
                  key={s}
                  onClick={() => setDefaultScale(s)}
                  type="button"
                >
                  {s === 1 ? '默认' : `${s}×`}
                </button>
              ))}
            </div>
          </Section>
        </div>
      </div>

      {retuneOpen && persona?.name && retuneInitial && (
        <PersonaRetune
          initial={retuneInitial}
          onClose={() => setRetuneOpen(false)}
          onSaved={() => {
            void hydratePersona()
          }}
        />
      )}
    </div>
  )
}

function Section({
  title,
  hint,
  children
}: {
  title: string
  hint?: string
  children: React.ReactNode
}): React.ReactElement {
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-white/80">{title}</p>
      {children}
      {hint && <p className="mt-1.5 text-[10px] text-white/30">{hint}</p>}
    </div>
  )
}
