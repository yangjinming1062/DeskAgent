import { IconDownload, IconRefresh, IconUpload, IconVolume } from '@tabler/icons-react'
import { useRef, useState } from 'react'

import { ConfirmDialog, Tip } from '@/shared/components/ui'
import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { triggerHaptic } from '@/shared/lib/haptics'
import { AudioLines, Info, KeyRound, Settings, Sparkles, Wrench } from '@/shared/lib/icons'
import { getSpiritAgentConfig, getSpiritAgentConfigDefaults, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { OverlayIconButton } from '../overlays/overlay-chrome'
import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { McpSettings } from './mcp-settings'
import { RunnerSettings } from './runner-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import { SpeechSettings } from './speech-settings'
import type { SettingsPageProps, SettingsView as SettingsViewId } from './types'
import { VoiceGallerySettings } from './voice-gallery-settings'

const SETTINGS_VIEWS = [
  'account',
  'speech',
  'voices',
  'runner',
  'skills',
  'mcp',
  'about'
] as const satisfies readonly SettingsViewId[]

export function SettingsView({ gateway, onClose, onConfigSaved }: SettingsPageProps): React.JSX.Element {
  const t = strings
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'account')

  const importInputRef = useRef<HTMLInputElement | null>(null)
  const [resetOpen, setResetOpen] = useState(false)

  const exportConfig = async () => {
    try {
      // Reuse the typed getter instead of the loose `Record<string, unknown>`
      // variant — both come from the same `/api/config` endpoint, but the
      // structured shape gives us better safety on round-trip without
      // changing the JSON.stringify behaviour (B1).
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
    // Refuse anything >2 MiB up front. The accept filter is a hint only —
    // the user can pick any file, and a large file.text() + JSON.parse on
    // the renderer thread would freeze the window with no feedback.
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
            icon={IconVolume}
            label={t.speech.title}
            onClick={() => setActiveView('speech')}
          />
          <OverlayNavItem
            active={activeView === 'voices'}
            icon={AudioLines}
            label={t.settings.nav.voices}
            onClick={() => setActiveView('voices')}
          />
          <OverlayNavItem
            active={activeView === 'runner'}
            icon={Settings}
            label={t.settings.nav.runner ?? 'Runner'}
            onClick={() => setActiveView('runner')}
          />
          <OverlayNavItem
            active={activeView === 'skills'}
            icon={Sparkles}
            label={t.settings.nav.skills ?? 'Skills'}
            onClick={() => setActiveView('skills')}
          />
          <div className="my-2 h-px bg-white/10" />
          <OverlayNavItem
            active={activeView === 'mcp'}
            icon={Wrench}
            label={t.settings.nav.mcp}
            onClick={() => setActiveView('mcp')}
          />
          <div className="my-2 h-px bg-white/10" />
          <OverlayNavItem
            active={activeView === 'about'}
            icon={Info}
            label={t.settings.nav.about}
            onClick={() => setActiveView('about')}
          />
          <div className="mt-auto flex items-center gap-1 pt-2">
            <Tip label={t.settings.exportConfig}>
              <OverlayIconButton onClick={() => void exportConfig()}>
                <IconDownload className="size-3.5" />
              </OverlayIconButton>
            </Tip>
            <Tip label={t.settings.importConfig}>
              <OverlayIconButton
                onClick={() => {
                  triggerHaptic('open')
                  importInputRef.current?.click()
                }}
              >
                <IconUpload className="size-3.5" />
              </OverlayIconButton>
            </Tip>
            <Tip label={t.settings.resetToDefaults}>
              <OverlayIconButton
                className="hover:text-destructive"
                onClick={() => {
                  triggerHaptic('warning')
                  setResetOpen(true)
                }}
              >
                <IconRefresh className="size-3.5" />
              </OverlayIconButton>
            </Tip>
          </div>
        </OverlaySidebar>

        <OverlayMain className="px-0 pb-0 pt-[calc(var(--titlebar-height)+1rem)]">
          {activeView === 'account' ? (
            <AccountSettings onConfigSaved={onConfigSaved} />
          ) : activeView === 'speech' ? (
            <SpeechSettings />
          ) : activeView === 'voices' ? (
            <VoiceGallerySettings />
          ) : activeView === 'runner' ? (
            <RunnerSettings />
          ) : activeView === 'skills' ? (
            <SkillsToolsTabs />
          ) : activeView === 'mcp' ? (
            <McpSettings gateway={gateway} onConfigSaved={onConfigSaved} />
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

          // Reset so selecting the same file twice still fires onChange.
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

export { SettingsView as SettingsPage }
