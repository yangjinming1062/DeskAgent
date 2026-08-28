import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { Seed3dWizard } from '@/companion/fullbody/seed3d-wizard'
import {
  $mesh2dInfo,
  $renderMode,
  type RenderMode,
  requestMesh2DGeneration,
  switchRenderMode
} from '@/companion/mesh2d/mesh2d-store'
import { $defaultScale, setDefaultScale } from '@/companion/spatial'
import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE, HINT_TEXT, Segmented, SettingsPage, Slider } from '@/shared/panel'

interface Seed3dWizardState {
  avatarId: number
  supportsMultiview: boolean
}

// 形象页：渲染模式（2D / 3D）、2D 动画资产状态与重试、桌面显示比例。
export function AppearancePage(): React.ReactElement {
  const renderMode = useStore($renderMode)
  const mesh2dInfo = useStore($mesh2dInfo)
  const defaultScale = useStore($defaultScale)
  const [seed3dWizard, setSeed3dWizard] = useState<Seed3dWizardState | null>(null)

  // 切 3D 前先补 3D 种子图：2D 正面种子的站姿与画风都不满足 3D 建模（A-pose、3D 画风），
  // 且只在这一刻才值得付生图（onboarding 只确认 2D 正面）。3D 正面缺或（多视角时）背面缺则出向导；
  // 非多视角供应商已有 3D 正面时直接切换。
  const onRenderModeClick = async (m: RenderMode): Promise<void> => {
    if (m === '3d' && renderMode !== '3d') {
      try {
        const res = await window.spiritagent.api<{
          id?: number
          seed_front_3d_url?: string | null
          seed_back_url?: string | null
          supports_multiview?: boolean
        }>({ path: '/api/companion/avatar' })

        if (res.id != null && (!res?.seed_front_3d_url || (res?.supports_multiview && !res?.seed_back_url))) {
          setSeed3dWizard({ avatarId: res.id, supportsMultiview: res.supports_multiview === true })

          return
        }
      } catch {
        // 头像行拉取失败时按直接切换处理；缺 3D 种子输入会在 3D 派发处以后端报错暴露，用户可重试。
      }
    }

    void switchRenderMode(m)
  }

  const mesh2dRetryable = mesh2dInfo.status !== 'succeeded' && mesh2dInfo.status !== 'generating'

  return (
    <>
      <SettingsPage title="形象">
        <section>
          <h3 className="text-xs font-medium text-white/80">渲染模式</h3>
          <p className={cn(HINT_TEXT, 'mt-1')}>
            切到 3D 会先在向导内逐张生成 3D
            正面立绘（多视角供应商再补一张背面立绘），每张均需手动点按触发；全部确认后触发云端 3D 模型生成（1~3
            分钟），生成期间显示 2D 动画版（或程序化蛋过渡）；生成失败永久保持 2D 动画版；切回 2D 立即生效。
          </p>
          <div className="mt-2.5">
            <Segmented<RenderMode>
              onChange={m => void onRenderModeClick(m)}
              options={[
                { value: '2d', label: '2D 动画版' },
                { value: '3d', label: '3D 立体版' }
              ]}
              value={renderMode}
            />
          </div>

          {/* DESIGN §5.5：2D 切分失败（或尚无 2D 资产）时提供重试入口 */}
          {renderMode === '2d' && mesh2dRetryable && (
            <div className="mt-2.5 flex items-center justify-between rounded-xl border border-white/8 bg-surface-card px-3.5 py-2.5">
              <span className="text-xs text-white/60">
                {mesh2dInfo.status === 'failed' ? '2D 动画资产生成失败' : '2D 动画资产尚未生成'}
              </span>
              <button
                className={cn(BTN_SUBTLE, 'h-7 px-3')}
                onClick={() => void requestMesh2DGeneration()}
                type="button"
              >
                重新切分
              </button>
            </div>
          )}
        </section>

        <section className="mt-6">
          <h3 className="text-xs font-medium text-white/80">形象大小</h3>
          <p className={cn(HINT_TEXT, 'mt-1')}>精灵在桌面上的默认显示比例。</p>
          <div className="mt-3 flex max-w-sm items-center gap-3">
            <Slider
              ariaLabel="形象大小"
              max={3}
              min={0.3}
              onChange={setDefaultScale}
              step={0.05}
              value={defaultScale}
            />
            <span className="w-11 shrink-0 text-right text-xs tabular-nums text-white/70">
              {String(Number(defaultScale.toFixed(2)))}×
            </span>
          </div>
          <p className={cn(HINT_TEXT, 'mt-1.5')}>0.3×–3× 连续可调，1× 为默认。</p>
        </section>
      </SettingsPage>

      {seed3dWizard != null && (
        <Seed3dWizard
          avatarId={seed3dWizard.avatarId}
          onCancel={() => setSeed3dWizard(null)}
          onConfirm={() => {
            setSeed3dWizard(null)
            void switchRenderMode('3d')
          }}
          supportsMultiview={seed3dWizard.supportsMultiview}
        />
      )}
    </>
  )
}
