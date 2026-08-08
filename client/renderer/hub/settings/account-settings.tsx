import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import {
  Button,
  ConfirmDialog
} from '@/shared/components/ui'
import { getDeskAgentConfig, saveDeskAgentConfig } from '@/shared/deskagent'
import { triggerHaptic } from '@/shared/lib/haptics'
import { KeyRound, Loader2, LogOut } from '@/shared/lib/icons'
import { buildSecretFieldBody } from '@/shared/lib/secret-field-body'
import { $auth, logout } from '@/shared/store/auth'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { DeskAgentConfigResponse } from '@/shared/types/deskagent'

import {
  AgentDefaultsSection,
  type AgentFormState
} from './account/agent-defaults-section'
import { ChangePasswordForm } from './account/change-password-form'
import {
  type ChatFormState,
  ContextCompressionSection
} from './account/context-compression-section'
import { type WebFormState, WebSearchSection } from './account/web-search-section'
import { ListRow, LoadingState, SectionHeading, SettingsContent } from './primitives'

const WEB_BACKEND_OPTIONS = ['ddgs', 'brave-free', 'tavily'] as const
const REASONING_OPTIONS = ['minimal', 'low', 'medium', 'high', 'max'] as const
// OpenAI service_tier accepts exactly {auto, default, flex}. UI must mirror
// the API's allowed set — anything outside is silently dropped by the
// backend's whitelist before reaching the model.
const SERVICE_TIER_OPTIONS = ['auto', 'default', 'flex'] as const

const EMPTY_WEB: WebFormState = {
  backend: 'ddgs',
  extract_backend: 'tavily',
  brave_api_key: '',
  brave_api_key_set: false,
  brave_api_key_fingerprint: '<empty>',
  cleared_brave: false,
  tavily_api_key: '',
  tavily_api_key_set: false,
  tavily_api_key_fingerprint: '<empty>',
  cleared_tavily: false,
  tavily_base_url: ''
}

const EMPTY_AGENT: AgentFormState = {
  reasoning_effort: 'medium',
  service_tier: 'auto',
  enable_background_review: true
}

const EMPTY_CHAT: ChatFormState = {
  enable_context_compression: true,
  context_compression_threshold: 0.7
}

const THRESHOLD_OPTIONS = ['0.5', '0.6', '0.7', '0.8', '0.9'] as const

const readWebState = (config: DeskAgentConfigResponse): WebFormState => {
  const web = config.web

  return {
    backend: web?.backend ?? EMPTY_WEB.backend,
    extract_backend: web?.extract_backend ?? EMPTY_WEB.extract_backend,
    brave_api_key: '',
    brave_api_key_set: web?.brave_api_key_set === true,
    brave_api_key_fingerprint: web?.brave_api_key_fingerprint ?? EMPTY_WEB.brave_api_key_fingerprint,
    cleared_brave: false,
    tavily_api_key: '',
    tavily_api_key_set: web?.tavily_api_key_set === true,
    tavily_api_key_fingerprint: web?.tavily_api_key_fingerprint ?? EMPTY_WEB.tavily_api_key_fingerprint,
    cleared_tavily: false,
    tavily_base_url: web?.tavily_base_url ?? EMPTY_WEB.tavily_base_url
  }
}

const readAgentState = (config: DeskAgentConfigResponse): AgentFormState => {
  const agent = config.agent

  return {
    reasoning_effort: agent?.reasoning_effort ?? EMPTY_AGENT.reasoning_effort,
    service_tier: agent?.service_tier ?? EMPTY_AGENT.service_tier,
    enable_background_review: agent?.enable_background_review ?? EMPTY_AGENT.enable_background_review
  }
}

const readChatState = (config: DeskAgentConfigResponse): ChatFormState => ({
  enable_context_compression: config.chat?.enable_context_compression ?? EMPTY_CHAT.enable_context_compression,
  context_compression_threshold: config.chat?.context_compression_threshold ?? EMPTY_CHAT.context_compression_threshold
})

export function AccountSettings({ onConfigSaved }: { onConfigSaved?: () => void } = {}): React.JSX.Element {
  const t = strings
  const a = t.settings.account
  const auth = useStore($auth)

  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [originalWeb, setOriginalWeb] = useState<WebFormState>(EMPTY_WEB)
  const [originalAgent, setOriginalAgent] = useState<AgentFormState>(EMPTY_AGENT)
  const [originalChat, setOriginalChat] = useState<ChatFormState>(EMPTY_CHAT)
  const [web, setWeb] = useState<WebFormState>(EMPTY_WEB)
  const [agent, setAgent] = useState<AgentFormState>(EMPTY_AGENT)
  const [chat, setChat] = useState<ChatFormState>(EMPTY_CHAT)
  const [clearingKey, setClearingKey] = useState<'brave_api_key' | 'tavily_api_key' | null>(null)
  const [signOutOpen, setSignOutOpen] = useState(false)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const config = await getDeskAgentConfig()

        if (cancelled) {
          return
        }

        const nextWeb = readWebState(config)
        const nextAgent = readAgentState(config)
        const nextChat = readChatState(config)
        setOriginalWeb(nextWeb)
        setOriginalAgent(nextAgent)
        setOriginalChat(nextChat)
        setWeb(nextWeb)
        setAgent(nextAgent)
        setChat(nextChat)
        setLoadError(null)
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : String(err))
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  const isWebDirty =
    web.cleared_brave ||
    web.cleared_tavily ||
    web.brave_api_key !== '' ||
    web.tavily_api_key !== '' ||
    web.backend !== originalWeb.backend ||
    web.extract_backend !== originalWeb.extract_backend ||
    web.tavily_base_url !== originalWeb.tavily_base_url

  const isAgentDirty =
    agent.reasoning_effort !== originalAgent.reasoning_effort ||
    agent.service_tier !== originalAgent.service_tier ||
    agent.enable_background_review !== originalAgent.enable_background_review

  const isChatDirty =
    chat.enable_context_compression !== originalChat.enable_context_compression ||
    chat.context_compression_threshold !== originalChat.context_compression_threshold

  const isDirty = isWebDirty || isAgentDirty || isChatDirty

  const updateWeb = (patch: Partial<WebFormState>) => {
    setWeb(prev => ({ ...prev, ...patch }))
  }

  const updateAgent = (patch: Partial<AgentFormState>) => {
    setAgent(prev => ({ ...prev, ...patch }))
  }

  const updateChat = (patch: Partial<ChatFormState>) => {
    setChat(prev => ({ ...prev, ...patch }))
  }

  const onApiKeyChange = (key: 'brave_api_key' | 'tavily_api_key', value: string) => {
    const clearedField = key === 'brave_api_key' ? 'cleared_brave' : 'cleared_tavily'
    setWeb(prev => ({ ...prev, [key]: value, [clearedField]: value === '' ? prev[clearedField] : false }))
  }

  const onClearApiKey = (key: 'brave_api_key' | 'tavily_api_key') => {
    setClearingKey(key)
  }

  const handleSave = async () => {
    try {
      setIsSaving(true)

      // Build the body shallowly from form state. Sensitive keys use 3-state logic:
      // typed non-empty → write value, cleared → write '', untouched → omit the key.
      const bodyWeb: Record<string, unknown> = {}
      bodyWeb.backend = web.backend
      bodyWeb.extract_backend = web.extract_backend
      bodyWeb.tavily_base_url = web.tavily_base_url

      const brave = buildSecretFieldBody(web.brave_api_key, web.cleared_brave, '')

      if (!brave.omit) {
        bodyWeb.brave_api_key = brave.value
      }

      const tavily = buildSecretFieldBody(web.tavily_api_key, web.cleared_tavily, '')

      if (!tavily.omit) {
        bodyWeb.tavily_api_key = tavily.value
      }

      const { config } = await saveDeskAgentConfig({
        agent: {
          enable_background_review: agent.enable_background_review,
          reasoning_effort: agent.reasoning_effort,
          service_tier: agent.service_tier
        },
        chat: {
          enable_context_compression: chat.enable_context_compression,
          context_compression_threshold: chat.context_compression_threshold
        },
        web: bodyWeb
      })

      const nextWeb = readWebState(config)
      const nextAgent = readAgentState(config)
      const nextChat = readChatState(config)
      setOriginalWeb(nextWeb)
      setOriginalAgent(nextAgent)
      setOriginalChat(nextChat)
      setWeb(nextWeb)
      setAgent(nextAgent)
      setChat(nextChat)
      triggerHaptic('success')
      notify({ kind: 'success', title: a.heading, message: a.saved })
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, a.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingState label={a.loading} />
      </SettingsContent>
    )
  }

  if (loadError) {
    return (
      <SettingsContent>
        <ListRow
          description={loadError}
          title={
            <div className="flex items-center gap-2 text-destructive">
              <KeyRound className="size-4" />
              <span>{a.heading}</span>
            </div>
          }
        />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <SectionHeading icon={KeyRound} title={a.heading} />

      <ChangePasswordForm />

      <div className="my-4 h-px bg-border/30" />

      <WebSearchSection
        disabled={isSaving}
        onApiKeyChange={onApiKeyChange}
        onClearApiKey={onClearApiKey}
        state={web}
        t={a.webSearch}
        update={updateWeb}
      />

      <div className="my-4 h-px bg-border/30" />

      <AgentDefaultsSection disabled={isSaving} state={agent} t={a.agentDefaults} update={updateAgent} />

      <div className="my-4 h-px bg-border/30" />

      <ContextCompressionSection disabled={isSaving} state={chat} t={a.contextCompression} update={updateChat} />

      <div className="my-4 h-px bg-border/30" />

      <ListRow
        action={
          <Button
            className="text-destructive hover:text-destructive"
            onClick={() => setSignOutOpen(true)}
            size="sm"
            variant="outline"
          >
            <LogOut className="size-3.5" />
            {a.signOut}
          </Button>
        }
        description={auth.kind === 'authenticated' ? (auth.snapshot.user?.username ?? '') : ''}
        title={a.signOut}
      />

      <div className="mt-8 flex justify-end">
        <Button disabled={isSaving || !isDirty} onClick={() => void handleSave()}>
          {isSaving ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {isSaving ? t.common.saving : t.common.save}
        </Button>
      </div>

      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={a.webSearch.clearKey}
        description={a.webSearch.clearKeyConfirm}
        onConfirm={() => {
          if (!clearingKey) {
            return
          }

          const clearedField = clearingKey === 'brave_api_key' ? 'cleared_brave' : 'cleared_tavily'
          setWeb(prev => ({ ...prev, [clearingKey]: '', [clearedField]: true }))
        }}
        onOpenChange={(open: boolean) => {
          if (!open) {
            setClearingKey(null)
          }
        }}
        open={clearingKey !== null}
        title={a.webSearch.clearKey}
        variant="destructive"
      />
      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={a.signOut}
        description={a.signOutConfirm}
        onConfirm={() => {
          void logout()
        }}
        onOpenChange={setSignOutOpen}
        open={signOutOpen}
        title={a.signOut}
        variant="destructive"
      />
    </SettingsContent>
  )
}

type AgentDefaultsCopy = (typeof strings)['settings']['account']['agentDefaults']
type ContextCompressionCopy = (typeof strings)['settings']['account']['contextCompression']

