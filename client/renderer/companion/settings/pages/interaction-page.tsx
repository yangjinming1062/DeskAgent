import { useStore } from '@nanostores/react'
import type React from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $effectiveTier,
  $userPreferredTier,
  type DisturbanceTier,
  setDisturbanceTier
} from '@/companion/companion-store'
import { pushDevLog } from '@/companion/developer-overlay'
import { DISTURBANCE_TIERS } from '@/companion/disturbance-tiers'
import {
  $llmAffect,
  $llmAutonomy,
  $llmReactions,
  $responseMode,
  $subtitles,
  type ResponseMode,
  setLlmAffect,
  setLlmAutonomy,
  setLlmReactions,
  setResponseMode,
  setSubtitles
} from '@/companion/prefs'
import { Check } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { HINT_TEXT, Segmented, SettingRow, SettingsPage, Toggle } from '@/shared/panel'

// 交互页：伙伴怎么回应（回应方式 / 打扰档位 / 智能反应与自主行为 / 通话字幕）。
export function InteractionPage(): React.ReactElement {
  const { requestGateway } = useGatewayRequest()
  const tier = useStore($userPreferredTier)
  const responseMode = useStore($responseMode)
  const llmReactions = useStore($llmReactions)
  const llmAffect = useStore($llmAffect)
  const llmAutonomy = useStore($llmAutonomy)
  const subtitles = useStore($subtitles)

  const selectTier = (id: DisturbanceTier): void => {
    const previous = $userPreferredTier.get()
    setDisturbanceTier(id)
    // 推送 EFFECTIVE 档位（含活动覆盖）以保证后端闸门与渲染层一致。
    // 否则在手动点击后，沉浸式焦点上下文会让后端在整个轮询周期内都保持 un-mute。
    const effectiveNow = $effectiveTier.get()
    // 后端拒绝时本地回滚档位。
    requestGateway('companion.set_disturbance_tier', { tier: effectiveNow }).catch(err => {
      setDisturbanceTier(previous)
      pushDevLog(
        'disturbance_tier_rejected',
        JSON.stringify({ requested: id, previous, error: String(err?.message || err) })
      )
    })
  }

  return (
    <SettingsPage hint="伙伴怎么回应你、什么时候可以打扰你。" title="交互">
      <section>
        <h3 className="text-xs font-medium text-white/80">对话回应方式</h3>
        <p className={cn(HINT_TEXT, 'mt-1')}>语音通话模式始终语音，不受此设置影响。</p>
        <div className="mt-2.5 max-w-xs">
          <Segmented<ResponseMode>
            onChange={setResponseMode}
            options={[
              { value: 'text', label: '默认文字' },
              { value: 'voice', label: '始终语音' }
            ]}
            value={responseMode}
          />
        </div>
      </section>

      <section className="mt-6">
        <h3 className="text-xs font-medium text-white/80">打扰档位</h3>
        <p className={cn(HINT_TEXT, 'mt-1')}>只约束伙伴的主动行为，你发起的交互不受限。</p>
        <div className="mt-2.5 overflow-hidden rounded-xl border border-white/8 bg-[#1c1c21]">
          {DISTURBANCE_TIERS.map(t => (
            <button
              className={cn(
                'flex w-full items-center justify-between border-b border-white/5 px-3.5 py-2.5 text-left text-xs transition last:border-b-0 hover:bg-white/5',
                tier === t.id && 'bg-[#6c8aff]/10'
              )}
              key={t.id}
              onClick={() => selectTier(t.id)}
              type="button"
            >
              <span className={cn('font-medium', tier === t.id ? 'text-white' : 'text-white/80')}>{t.label}</span>
              <span className="flex items-center gap-1.5 text-white/40">
                {t.hint}
                {tier === t.id && <Check className="size-3.5 text-[#6c8aff]" />}
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="mt-6">
        <h3 className="text-xs font-medium text-white/80">智能反应与自主行为</h3>
        <p className={cn(HINT_TEXT, 'mt-1')}>让伙伴具备更智能的思考与决策能力；关闭可降低 LLM 调用消耗。</p>
        <div className="mt-2.5 divide-y divide-white/5 overflow-hidden rounded-xl border border-white/8 bg-[#1c1c21]">
          <SettingRow
            description="戳击时由 LLM 生成反应文案与表情（关闭使用预制反馈）；拖拽始终使用本地预制反馈"
            label="戳击思考回应"
          >
            <Toggle ariaLabel="戳击思考回应" checked={llmReactions} onChange={setLlmReactions} />
          </SettingRow>
          <SettingRow description="空闲 30 分钟以上时由 LLM 决定是否触发情境化表情" label="空闲情境情绪">
            <Toggle ariaLabel="空闲情境情绪" checked={llmAffect} onChange={setLlmAffect} />
          </SettingRow>
          <SettingRow description="自主档下由 LLM 决定漫游/栖身（关闭按本地规则）" label="自主空间决策">
            <Toggle ariaLabel="自主空间决策" checked={llmAutonomy} onChange={setLlmAutonomy} />
          </SettingRow>
        </div>
      </section>

      <section className="mt-6">
        <h3 className="text-xs font-medium text-white/80">通话字幕</h3>
        <div className="mt-2.5 divide-y divide-white/5 overflow-hidden rounded-xl border border-white/8 bg-[#1c1c21]">
          <SettingRow description="语音通话模式下显示双向字幕（通话面板内也有快捷开关）" label="显示通话字幕">
            <Toggle ariaLabel="显示通话字幕" checked={subtitles} onChange={setSubtitles} />
          </SettingRow>
        </div>
      </section>
    </SettingsPage>
  )
}
