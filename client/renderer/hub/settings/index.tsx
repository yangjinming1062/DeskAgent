import { IconDownload, IconRefresh, IconUpload } from '@tabler/icons-react'
import { useRef, useState } from 'react'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { triggerHaptic } from '@/shared/lib/haptics'
import { AudioLines, Cpu, Info, KeyRound, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_ICON, ConfirmDialog } from '@/shared/panel'
import { getSpiritAgentConfig, getSpiritAgentConfigDefaults, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { RunnerSettings } from './runner-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import { SpeechSettings } from './speech-settings'
import type { SettingsPageProps, SettingsView as SettingsViewId } from './types'

const SETTINGS_VIEWS = ['account', 'speech', 'runner', 'skills', 'about'] as const satisfies readonly SettingsViewId[]

export function SettingsView({ onClose, onConfigSaved }: SettingsPageProps): React.JSX.Element {
  const t = strings
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'account')

  const importInputRef = useRef<HTMLInputElement | null>(null)
  const [resetOpen, setResetOpen] = useState(false)

  const exportConfig = async () => {
    try {
      // 复用强类型 getter，而非宽松的 `Record<string, unknown>` 变体——
      // 两者都来自同一 `/api/config` 端点，但结构化形态在往返时更安全，
      // 且不改变 JSON.stringify 行为（B1）。
      const cfg = await getSpiritAgentConfig()
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'spiritagent-config.json'
      a.click()
      URL.revokeObjectURL(url)
      triggerHaptic('success')
    } catch (err) {
      notifyError(err, t.settings.exportFailed)
    }
  }

  const importConfig = async (file: File) => {
    // 预先拒绝 >2 MiB 的文件。accept 过滤器只是提示——用户可以选任意文件，
    // 在渲染线程上对大文件做 file.text() + JSON.parse 会冻住窗口且无反馈。
    const MAX_IMPORT_BYTES = 2 * 1024 * 1024

    if (file.size > MAX_IMPORT_BYTES) {
      notifyError(new Error('config too large'), t.settings.importFailed)

      return
    }

    try {
      const text = await file.text()
      const parsed = JSON.parse(text) as unknown

      if (!parsed || typeof parsed !== 'object') {
        notifyError(new Error('invalid config shape'), t.settings.importFailed)

        return
      }

      await saveSpiritAgentConfig(parsed as Parameters<typeof saveSpiritAgentConfig>[0])
      triggerHaptic('success')
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, t.settings.importFailed)
    }
  }

  const resetConfig = async () => {
    try {
      await saveSpiritAgentConfig(await getSpiritAgentConfigDefaults())
      triggerHaptic('success')
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, t.settings.resetFailed)
    }
  }

  return (
    <OverlayView closeLabel={t.settings.closeSettings} onClose={onClose}>
      <OverlaySplitLayout>
        <OverlaySidebar>
          <OverlayNavItem
            active={activeView === 'account'}
            icon={KeyRound}
            label={t.settings.nav.account}
            onClick={() => setActiveView('account')}
          />
          <OverlayNavItem
            active={activeView === 'speech'}
            icon={AudioLines}
            label={t.speech.title}
            onClick={() => setActiveView('speech')}
          />
          <OverlayNavItem
            active={activeView === 'runner'}
            icon={Cpu}
            label={t.settings.nav.runner}
            onClick={() => setActiveView('runner')}
          />
          <OverlayNavItem
            active={activeView === 'skills'}
            icon={Sparkles}
            label={t.settings.nav.skills}
            onClick={() => setActiveView('skills')}
          />
          <div className="my-2 h-px bg-white/8" />
          <OverlayNavItem
            active={activeView === 'about'}
            icon={Info}
            label={t.settings.nav.about}
            onClick={() => setActiveView('about')}
          />
          <div className="mt-auto flex items-center gap-0.5 pt-2">
            <button
              aria-label={t.settings.exportConfig}
              className={BTN_ICON}
              onClick={() => void exportConfig()}
              title={t.settings.exportConfig}
              type="button"
            >
              <IconDownload className="size-3.5" />
            </button>
            <button
              aria-label={t.settings.importConfig}
              className={BTN_ICON}
              onClick={() => {
                triggerHaptic('open')
                importInputRef.current?.click()
              }}
              title={t.settings.importConfig}
              type="button"
            >
              <IconUpload className="size-3.5" />
            </button>
            <button
              aria-label={t.settings.resetToDefaults}
              className={cn(BTN_ICON, 'hover:text-rose-300')}
              onClick={() => {
                triggerHaptic('warning')
                setResetOpen(true)
              }}
              title={t.settings.resetToDefaults}
              type="button"
            >
              <IconRefresh className="size-3.5" />
            </button>
          </div>
        </OverlaySidebar>

        <OverlayMain className="px-0 pb-0 pt-[calc(var(--titlebar-height)+1rem)]">
          {activeView === 'account' ? (
            <AccountSettings onConfigSaved={onConfigSaved} />
          ) : activeView === 'speech' ? (
            <SpeechSettings />
          ) : activeView === 'runner' ? (
            <RunnerSettings />
          ) : activeView === 'skills' ? (
            <SkillsToolsTabs />
          ) : (
            <AboutSettings />
          )}
        </OverlayMain>
      </OverlaySplitLayout>
      <input
        accept="application/json,.json"
        hidden
        onChange={e => {
          const file = e.target.files?.[0]

          if (file) {
            void importConfig(file)
          }

          // 重置输入，连选同一文件也能再次触发 onChange。
          e.target.value = ''
        }}
        ref={importInputRef}
        type="file"
      />
      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={t.settings.resetToDefaults}
        description={t.settings.resetConfirm}
        onConfirm={() => {
          void resetConfig()
        }}
        onOpenChange={setResetOpen}
        open={resetOpen}
        title={t.settings.resetToDefaults}
        variant="destructive"
      />
    </OverlayView>
  )
}
