import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { InlineNotice } from '@/shared/components/notifications'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Switch } from '@/shared/components/ui/switch'
import { getDeskAgentConfig, saveDeskAgentConfig } from '@/shared/deskagent/config'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Archive, Eye, EyeOff, Globe, KeyRound, Loader2, LogOut, SlidersHorizontal, X } from '@/shared/lib/icons'
import { $auth, logout } from '@/shared/store/auth'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { DeskAgentConfigResponse } from '@/shared/types/deskagent'

import { ListRow, LoadingState, SectionHeading, SettingsContent, SettingsSubsection } from './primitives'

const WEB_BACKEND_OPTIONS = ['ddgs', 'brave-free', 'tavily'] as const
const EXTRACT_BACKEND_OPTIONS = ['tavily', 'brave-free', 'ddgs'] as const
const REASONING_OPTIONS = ['minimal', 'low', 'medium', 'high', 'max'] as const
const SERVICE_TIER_OPTIONS = ['standard', 'fast', 'priority', 'on', 'auto'] as const

interface WebFormState {
  backend: string
  extract_backend: string
  brave_api_key: string
  brave_api_key_set: boolean
  brave_api_key_fingerprint: string
  cleared_brave: boolean
  tavily_api_key: string
  tavily_api_key_set: boolean
  tavily_api_key_fingerprint: string
  cleared_tavily: boolean
  tavily_base_url: string
}

interface AgentFormState {
  reasoning_effort: string
  service_tier: string
  enable_background_review: boolean
  // Display preference — lives under `display.*` in /api/config, not
  // `agent.*`. Grouped into the agent defaults section UI to avoid a
  // new section header; the save handler writes it to the display block.
  show_subagents_in_sidebar: boolean
}

interface ChatFormState {
  enable_context_compression: boolean
  context_compression_threshold: number
}

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
  service_tier: 'standard',
  enable_background_review: true,
  show_subagents_in_sidebar: false
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
  const display = config.display

  return {
    reasoning_effort: agent?.reasoning_effort ?? EMPTY_AGENT.reasoning_effort,
    service_tier: agent?.service_tier ?? EMPTY_AGENT.service_tier,
    enable_background_review: agent?.enable_background_review ?? EMPTY_AGENT.enable_background_review,
    show_subagents_in_sidebar: display?.show_subagents_in_sidebar ?? EMPTY_AGENT.show_subagents_in_sidebar
  }
}

const readChatState = (config: DeskAgentConfigResponse): ChatFormState => ({
  enable_context_compression: config.chat?.enable_context_compression ?? EMPTY_CHAT.enable_context_compression,
  context_compression_threshold: config.chat?.context_compression_threshold ?? EMPTY_CHAT.context_compression_threshold
})

export function AccountSettings({ onConfigSaved }: { onConfigSaved?: () => void } = {}) {
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
    agent.enable_background_review !== originalAgent.enable_background_review ||
    agent.show_subagents_in_sidebar !== originalAgent.show_subagents_in_sidebar

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
    if (!window.confirm(a.webSearch.clearKeyConfirm)) {
      return
    }

    const clearedField = key === 'brave_api_key' ? 'cleared_brave' : 'cleared_tavily'
    setWeb(prev => ({ ...prev, [key]: '', [clearedField]: true }))
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

      if (web.brave_api_key !== '') {
        bodyWeb.brave_api_key = web.brave_api_key
      } else if (web.cleared_brave) {
        bodyWeb.brave_api_key = ''
      }

      if (web.tavily_api_key !== '') {
        bodyWeb.tavily_api_key = web.tavily_api_key
      } else if (web.cleared_tavily) {
        bodyWeb.tavily_api_key = ''
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
        display: {
          show_subagents_in_sidebar: agent.show_subagents_in_sidebar
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
            onClick={() => {
              if (window.confirm(a.signOutConfirm)) {
                void logout()
              }
            }}
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
    </SettingsContent>
  )
}

function ChangePasswordForm() {
  const t = strings
  const a = t.settings.account.changePassword

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)

  const handleSubmit = async () => {
    if (busy) {
      return
    }

    if (newPassword.length < 8) {
      notify({ kind: 'error', title: a.title, message: a.tooShort })

      return
    }

    if (newPassword !== confirm) {
      notify({ kind: 'error', title: a.title, message: a.mismatch })

      return
    }

    if (currentPassword === newPassword) {
      notify({ kind: 'error', title: a.title, message: a.sameAsOld })

      return
    }

    setBusy(true)

    try {
      const result = await window.deskagent.changePassword({
        current_password: currentPassword,
        new_password: newPassword
      })

      notify({ kind: 'success', title: a.title, message: result.message || a.success })
      setCurrentPassword('')
      setNewPassword('')
      setConfirm('')
    } catch (err) {
      notifyError(err, a.title)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ListRow
      description={a.title}
      title={
        <div className="flex items-center gap-3">
          <Input
            className="max-w-xs"
            disabled={busy}
            onChange={event => setCurrentPassword(event.currentTarget.value)}
            placeholder={a.currentPassword}
            type="password"
            value={currentPassword}
          />
          <Input
            className="max-w-xs"
            disabled={busy}
            onChange={event => setNewPassword(event.currentTarget.value)}
            placeholder={a.newPassword}
            type="password"
            value={newPassword}
          />
          <Input
            className="max-w-xs"
            disabled={busy}
            onChange={event => setConfirm(event.currentTarget.value)}
            placeholder={a.confirmPassword}
            type="password"
            value={confirm}
          />
          <Button
            disabled={busy || !currentPassword || !newPassword || !confirm}
            onClick={() => void handleSubmit()}
            size="sm"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {a.submit}
          </Button>
        </div>
      }
      wide
    />
  )
}

type WebSearchCopy = (typeof strings)['settings']['account']['webSearch']
type AgentDefaultsCopy = (typeof strings)['settings']['account']['agentDefaults']
type ContextCompressionCopy = (typeof strings)['settings']['account']['contextCompression']

function WebSearchSection({
  disabled,
  onApiKeyChange,
  onClearApiKey,
  state,
  t,
  update
}: {
  disabled: boolean
  onApiKeyChange: (key: 'brave_api_key' | 'tavily_api_key', value: string) => void
  onClearApiKey: (key: 'brave_api_key' | 'tavily_api_key') => void
  state: WebFormState
  t: WebSearchCopy
  update: (patch: Partial<WebFormState>) => void
}) {
  return (
    <SettingsSubsection icon={Globe} intro={t.intro} title={t.heading}>
      <ListRow
        action={
          <Select disabled={disabled} onValueChange={value => update({ backend: value })} value={state.backend}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WEB_BACKEND_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.backendOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.backendDesc}
        title={t.backend}
      />

      <ListRow
        action={
          <Select
            disabled={disabled}
            onValueChange={value => update({ extract_backend: value })}
            value={state.extract_backend}
          >
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EXTRACT_BACKEND_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.extractBackendOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.extractBackendDesc}
        title={t.extractBackend}
      />

      {computeNotices(state, t).map(message => (
        <InlineNotice key={message} kind="warning">
          {message}
        </InlineNotice>
      ))}

      <ApiKeyField
        copy={t}
        description={t.braveApiKeyDesc}
        disabled={disabled}
        fingerprint={state.brave_api_key_fingerprint}
        isSet={state.brave_api_key_set}
        onChange={value => onApiKeyChange('brave_api_key', value)}
        onClear={() => onClearApiKey('brave_api_key')}
        placeholder={t.braveApiKeyPlaceholder}
        title={t.braveApiKey}
        value={state.brave_api_key}
      />

      <ApiKeyField
        copy={t}
        description={t.tavilyApiKeyDesc}
        disabled={disabled}
        fingerprint={state.tavily_api_key_fingerprint}
        isSet={state.tavily_api_key_set}
        onChange={value => onApiKeyChange('tavily_api_key', value)}
        onClear={() => onClearApiKey('tavily_api_key')}
        placeholder={t.tavilyApiKeyPlaceholder}
        title={t.tavilyApiKey}
        value={state.tavily_api_key}
      />

      <ListRow
        action={
          <Input
            className="max-w-sm"
            disabled={disabled}
            onChange={event => update({ tavily_base_url: event.currentTarget.value })}
            placeholder={t.tavilyBaseUrlPlaceholder}
            value={state.tavily_base_url}
          />
        }
        title={t.tavilyBaseUrl}
      />
    </SettingsSubsection>
  )
}

function AgentDefaultsSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: AgentFormState
  t: AgentDefaultsCopy
  update: (patch: Partial<AgentFormState>) => void
}) {
  return (
    <SettingsSubsection icon={SlidersHorizontal} intro={t.intro} title={t.heading}>
      <ListRow
        action={
          <Select
            disabled={disabled}
            onValueChange={value => update({ reasoning_effort: value })}
            value={state.reasoning_effort}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REASONING_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.reasoningOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.reasoningEffortDesc}
        title={t.reasoningEffort}
      />

      <ListRow
        action={
          <Select
            disabled={disabled}
            onValueChange={value => update({ service_tier: value })}
            value={state.service_tier}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SERVICE_TIER_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.serviceTierOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.serviceTierDesc}
        title={t.serviceTier}
      />

      <ListRow
        action={
          <Switch
            checked={state.enable_background_review}
            disabled={disabled}
            onCheckedChange={value => update({ enable_background_review: value })}
          />
        }
        description={t.backgroundReviewDesc}
        title={t.backgroundReview}
      />

      <ListRow
        action={
          <Switch
            checked={state.show_subagents_in_sidebar}
            disabled={disabled}
            onCheckedChange={value => update({ show_subagents_in_sidebar: value })}
          />
        }
        description={t.showSubagentsInSidebarDesc}
        title={t.showSubagentsInSidebar}
      />
    </SettingsSubsection>
  )
}

function ContextCompressionSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: ChatFormState
  t: ContextCompressionCopy
  update: (patch: Partial<ChatFormState>) => void
}) {
  return (
    <SettingsSubsection icon={Archive} intro={t.intro} title={t.heading}>
      <ListRow
        action={
          <Switch
            checked={state.enable_context_compression}
            disabled={disabled}
            onCheckedChange={value => update({ enable_context_compression: value })}
          />
        }
        description={t.enableCompressionDesc}
        title={t.enableCompression}
      />
      <ListRow
        action={
          <Select
            disabled={disabled}
            onValueChange={value => update({ context_compression_threshold: Number(value) })}
            value={String(state.context_compression_threshold)}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {THRESHOLD_OPTIONS.map(opt => (
                <SelectItem key={opt} value={opt}>
                  {t.thresholdOptions[opt]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={t.thresholdDesc}
        title={t.threshold}
      />
    </SettingsSubsection>
  )
}

function ApiKeyField({
  copy,
  description,
  disabled,
  fingerprint,
  isSet,
  onChange,
  onClear,
  placeholder,
  title,
  value
}: {
  copy: WebSearchCopy
  description: string
  disabled: boolean
  fingerprint: string
  isSet: boolean
  onChange: (value: string) => void
  onClear: () => void
  placeholder: string
  title: string
  value: string
}) {
  const [revealed, setRevealed] = useState(false)
  const status = isSet ? copy.set : copy.notSet

  return (
    <ListRow
      action={
        <div className="flex items-center gap-2">
          <Input
            className="max-w-sm"
            disabled={disabled}
            onChange={event => onChange(event.currentTarget.value)}
            placeholder={placeholder}
            type={revealed ? 'text' : 'password'}
            value={value}
          />
          <Button
            aria-label={revealed ? copy.hide : copy.reveal}
            disabled={disabled}
            onClick={() => setRevealed(prev => !prev)}
            size="icon"
            type="button"
            variant="ghost"
          >
            {revealed ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </Button>
          {isSet ? (
            <Button
              aria-label={copy.clearKey}
              disabled={disabled}
              onClick={onClear}
              size="icon"
              type="button"
              variant="ghost"
            >
              <X className="size-4" />
            </Button>
          ) : null}
        </div>
      }
      description={description}
      hint={isSet ? copy.fingerprint(fingerprint) : undefined}
      title={
        <div className="flex items-center gap-2">
          <span>{title}</span>
          <span className="text-[length:var(--conversation-caption-font-size)] font-normal text-(--ui-text-tertiary)">
            · {status}
          </span>
        </div>
      }
    />
  )
}

function computeNotices(state: WebFormState, t: WebSearchCopy): string[] {
  const notices: string[] = []
  const extractIsTavily = state.extract_backend === 'tavily'
  const tavilyMissing = !state.tavily_api_key_set

  // One extract-related notice; the two negative conditions stay mutually
  // exclusive because the inner ternary collapses (extract_backend, key_set)
  // → one of three keys or none.
  if (extractIsTavily && tavilyMissing) {
    notices.push(t.unavailable.extractTavilyNoKey)
  } else if (!extractIsTavily && tavilyMissing) {
    notices.push(t.unavailable.extractNonTavilyNoKey)
  } else if (!extractIsTavily) {
    notices.push(t.unavailable.extractNonTavilyWithKey)
  }

  // Search: only surface the Tavily-missing-key banner when no extract notice
  // already covers the same root cause — the user already knows to add a key.
  if (state.backend === 'brave-free' && !state.brave_api_key_set) {
    notices.push(t.unavailable.searchKeyFallback('Brave'))
  } else if (state.backend === 'tavily' && tavilyMissing && !notices.length) {
    notices.push(t.unavailable.searchKeyFallback('Tavily'))
  }

  return notices
}
