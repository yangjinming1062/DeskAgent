import { IconDownload, IconRefresh, IconUpload, IconVolume } from '@tabler/icons-react'
import { useRef } from 'react'

import { Tip } from '@/shared/components/ui/tooltip'
import { getDeskAgentConfigDefaults, getDeskAgentConfigRecord, saveDeskAgentConfig } from '@/shared/deskagent'
import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { triggerHaptic } from '@/shared/lib/haptics'
import { AudioLines, Brain, Info, KeyRound, Settings, Sparkles, Wrench } from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { OverlayIconButton } from '../overlays/overlay-chrome'
import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

import { AboutSettings } from './about-settings'
import { AccountSettings } from './account-settings'
import { McpSettings } from './mcp-settings'
import { ModelConfigSettings } from './model-config-settings'
import { RunnerSettings } from './runner-settings'
import { SkillsToolsTabs } from './skills-tools-tabs'
import { SpeechSettings } from './speech-settings'
import type { SettingsPageProps } from './types'
import { VoiceGallerySettings } from './voice-gallery-settings'

type SettingsViewId = 'about' | 'account' | 'mcp' | 'models' | 'runner' | 'skills' | 'speech' | 'voices'

const SETTINGS_VIEWS: readonly SettingsViewId[] = [
  'account',
  'speech',
  'voices',
  'models',
  'runner',
  'skills',
  'mcp',
  'about'
]

export function SettingsView({ gateway, onClose, onConfigSaved }: SettingsPageProps) {
  const t = strings
  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'account')

  const importInputRef = useRef<HTMLInputElement | null>(null)

  const exportConfig = async () => {
    try {
      const cfg = await getDeskAgentConfigRecord()
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'deskagent-config.json'
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

      await saveDeskAgentConfig(parsed as Parameters<typeof saveDeskAgentConfig>[0])
      triggerHaptic('success')
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, t.settings.importFailed)
    }
  }

  const resetConfig = async () => {
    if (!window.confirm(t.settings.resetConfirm)) {
      return
    }

    try {
      await saveDeskAgentConfig(await getDeskAgentConfigDefaults())
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
            active={activeView === 'models'}
            icon={Brain}
            label={t.settings.nav.models}
            onClick={() => setActiveView('models')}
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
          <div className="my-2 h-px bg-border/30" />
          <OverlayNavItem
            active={activeView === 'mcp'}
            icon={Wrench}
            label={t.settings.nav.mcp}
            onClick={() => setActiveView('mcp')}
          />
          <div className="my-2 h-px bg-border/30" />
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
                  void resetConfig()
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
          ) : activeView === 'models' ? (
            <ModelConfigSettings gateway={gateway} onConfigSaved={onConfigSaved} />
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
    </OverlayView>
  )
}

export { SettingsView as SettingsPage }
