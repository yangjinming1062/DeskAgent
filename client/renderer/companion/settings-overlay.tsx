import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $effectiveTier, $userPreferredTier, setDisturbanceTier } from '@/companion/companion-store'
import { DISTURBANCE_TIERS } from '@/companion/disturbance-tiers'
import { usePanelDrag } from '@/companion/hooks/use-panel-drag'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import {
  $mesh2dInfo,
  $renderMode,
  type RenderMode,
  requestMesh2DGeneration,
  switchRenderMode
} from '@/companion/mesh2d/mesh2d-store'
import { PersonaRetune } from '@/companion/persona-retune'
import { $persona } from '@/companion/persona-store'
import {
  $companionVoiceId,
  $llmAffect,
  $llmAutonomy,
  $llmReactions,
  $responseMode,
  type ResponseMode,
  setCompanionVoiceId,
  setLlmAffect,
  setLlmAutonomy,
  setLlmReactions,
  setResponseMode
} from '@/companion/prefs'
import { $defaultScale, setDefaultScale } from '@/companion/spatial'
import { speakScripted } from '@/companion/tts'
import {
  designVoice,
  fetchVoiceCatalogRaw,
  GENDER_OPTIONS,
  LANGUAGE_LABELS,
  playDataUrl,
  sampleLine,
  type VoiceCatalog,
  type VoiceDesignPreview
} from '@/companion/voice'
import { Codicon } from '@/shared/components/ui'
import { SlidersHorizontal } from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'

import { pushDevLog } from './developer-overlay'
import { PersonaSection } from './persona-editor'

interface SettingsOverlayProps {
  onClose: () => void
}

const TIERS = DISTURBANCE_TIERS

// 伙伴专属设置放在精灵窗口（WS 网关在此启动）而非框架化的工具窗口，
// 因为 voice/clip/avatar 调用都是经网关走的 JSON-RPC。通用应用设置仍放在托盘工具窗口。
// 覆盖 plan §6 的伙伴条目：音色、形象、回应方式、档位。
export function CompanionSettings({ onClose }: SettingsOverlayProps): React.ReactElement {
  const tier = useStore($userPreferredTier)
  const responseMode = useStore($responseMode)
  const renderMode = useStore($renderMode)
  const mesh2dInfo = useStore($mesh2dInfo)
  const llmReactions = useStore($llmReactions)
  const llmAffect = useStore($llmAffect)
  const llmAutonomy = useStore($llmAutonomy)
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

  const [retuneOpen, setRetuneOpen] = useState(false)

  const [retuneInitial, setRetuneInitial] = useState<{
    name: string
    personality: string
    speaking_style: string
    background: string
    user_call_name: string
    user_gender: string
    user_age_bucket: string
    user_hobbies: string
    user_freeform: string
  } | null>(null)

  // 在弹出对话框之前从后端水合 retune 向导的 user_* 步骤。
  // 没有这一步，步骤 5 会显示空白字段，复核界面也会渲染成 '—'，
  // 不能反映真实的已保存状态。
  const openRetune = async () => {
    // A11：去重并发点击——快速连点两次会触发两次并行的 `get_user_profile`
    // 请求，都会写入 `retuneInitial`。后者覆盖前者，但前一次拉取的工作白做。
    if (retuneOpen) {
      return
    }

    setRetuneOpen(true)

    try {
      const profile = (await requestGateway<Record<string, string>>('companion.get_user_profile', {})) ?? {}

      setRetuneInitial({
        name: persona?.name ?? '',
        personality: persona?.personality ?? '',
        speaking_style: persona?.speakingStyle ?? '',
        background: persona?.background ?? '',
        user_call_name: profile.user_call_name ?? '',
        user_gender: profile.user_gender ?? '',
        user_age_bucket: profile.user_age_bucket ?? '',
        user_hobbies: profile.user_hobbies ?? '',
        user_freeform: profile.user_freeform ?? ''
      })
    } catch (err) {
      // C1：拒绝用空的 user_* 值打开——向导保存表单当前内容，
      // 若用空值兜底会把空串 PUT 覆盖用户已保存的 `user_call_name`、
      // `user_gender`、`user_hobbies` 等，悄无声息地把数据抹掉。
      // 直接关闭对话框并提示，等后端可达后再试。
      setRetuneOpen(false)
      notifyError(err, '暂时拉不到个人资料，稍后再试')
    }
  }

  const [showDesign, setShowDesign] = useState(false)
  const [designPrompt, setDesignPrompt] = useState('')
  const [designPreview, setDesignPreview] = useState<VoiceDesignPreview | null>(null)
  const [designing, setDesigning] = useState(false)
  const [designHint, setDesignHint] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('companion-settings', panelRef)
  const { bind: dragBind, storedOffset } = usePanelDrag('da.companion.settingsOffset', () => panelRef.current)

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
    void fetchVoiceCatalogRaw(requestGateway).then(r => {
      if (r.ok) {
        setCatalog(r.catalog)
      }
    })
  }, [requestGateway])

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

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center px-6 pb-10" style={{ pointerEvents: 'none' }}>
      <div
        className="flex h-[min(70vh,600px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/60 text-white shadow-2xl backdrop-blur-md"
        ref={panelRef}
        style={{
          pointerEvents: 'auto',
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined
        }}
      >
        <div
          className="flex cursor-grab items-center justify-between border-b border-white/10 px-5 py-3.5 active:cursor-grabbing"
          {...dragBind}
          title="拖动以移动面板"
        >
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="size-4 text-white/60" />
            <h2 className="text-sm font-semibold">伙伴设置</h2>
          </div>
          <button
            aria-label="关闭"
            className="flex size-7 items-center justify-center rounded-lg text-white/50 transition-colors hover:bg-white/10 hover:text-white"
            onClick={onClose}
            type="button"
          >
            <Codicon name="close" size="0.875rem" />
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

          {/* Render mode */}
          <Section
            hint="切到 3D 会触发云端生成（1~3 分钟），生成期间显示 2D 动画版（或程序化蛋过渡）；生成失败永久保持 2D 动画版；切回 2D 立即生效。"
            title="渲染模式"
          >
            <div className="flex gap-2">
              {(['2d', '3d'] as RenderMode[]).map(m => (
                <button
                  className={`flex-1 rounded-lg border px-3 py-2 text-xs transition ${renderMode === m ? 'border-white/60 bg-white/15 font-medium' : 'border-white/15 bg-white/5 text-white/70 hover:bg-white/10'}`}
                  key={m}
                  onClick={() => void switchRenderMode(m)}
                  type="button"
                >
                  {m === '2d' ? '2D 动画版' : '3D 立体版'}
                </button>
              ))}
            </div>
            {/* DESIGN §5.5：2D 切分失败（或尚无 2D 资产）时提供重试入口 */}
            {renderMode === '2d' && mesh2dInfo.status !== 'succeeded' && mesh2dInfo.status !== 'generating' && (
              <div className="mt-2 flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs">
                <span className="text-white/60">
                  {mesh2dInfo.status === 'failed' ? '2D 动画资产生成失败' : '2D 动画资产尚未生成'}
                </span>
                <button
                  className="rounded-full border border-white/25 px-3 py-1 text-white/80 transition hover:bg-white/10"
                  onClick={() => void requestMesh2DGeneration()}
                  type="button"
                >
                  重新切分
                </button>
              </div>
            )}
          </Section>

          {/* Advanced Reaction Switches */}
          <Section hint="让伙伴具备更智能的思考与决策能力；关闭可降低 LLM 调用消耗" title="智能反应与自主行为">
            <div className="space-y-2 text-xs">
              <label className="flex cursor-pointer items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 transition hover:bg-white/10">
                <div>
                  <p className="font-medium text-white/90">戳击思考回应</p>
                  <p className="text-[10px] text-white/40">
                    戳击时由 LLM 生成反应文案与表情（关闭使用预制反馈）；拖拽始终使用本地预制反馈
                  </p>
                </div>
                <input
                  checked={llmReactions}
                  className="h-4 w-4 rounded border-white/30 bg-white/10 text-emerald-500 focus:ring-0"
                  onChange={e => setLlmReactions(e.target.checked)}
                  type="checkbox"
                />
              </label>

              <label className="flex cursor-pointer items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 transition hover:bg-white/10">
                <div>
                  <p className="font-medium text-white/90">空闲情境情绪</p>
                  <p className="text-[10px] text-white/40">空闲 30 分钟以上时由 LLM 决定是否触发情境化表情</p>
                </div>
                <input
                  checked={llmAffect}
                  className="h-4 w-4 rounded border-white/30 bg-white/10 text-emerald-500 focus:ring-0"
                  onChange={e => setLlmAffect(e.target.checked)}
                  type="checkbox"
                />
              </label>

              <label className="flex cursor-pointer items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 transition hover:bg-white/10">
                <div>
                  <p className="font-medium text-white/90">自主空间决策</p>
                  <p className="text-[10px] text-white/40">由 LLM 决定什么时候睡觉/漫游/栖身（关闭按本地规则）</p>
                </div>
                <input
                  checked={llmAutonomy}
                  className="h-4 w-4 rounded border-white/30 bg-white/10 text-emerald-500 focus:ring-0"
                  onChange={e => setLlmAutonomy(e.target.checked)}
                  type="checkbox"
                />
              </label>
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
                    // 推送 EFFECTIVE 档位（含活动覆盖）以保证后端闸门与渲染层一致。
                    // 否则在手动点击后，沉浸式焦点上下文会让后端在整个
                    // 轮询周期内都保持 un-mute。
                    const effectiveNow = $effectiveTier.get()
                    // 后端拒绝时本地回滚档位。
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
                          onClick={() =>
                            void speakScripted(sampleLine(persona?.name ?? ''), v.id || undefined, 'voice.preview')
                          }
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
        <PersonaRetune initial={retuneInitial} onClose={() => setRetuneOpen(false)} />
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
