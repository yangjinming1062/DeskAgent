import { useEffect, useState } from 'react'

import { InlineNotice } from '@/shared/components/notifications'
import { Button } from '@/shared/components/ui/button'
import { ConfirmDialog } from '@/shared/components/ui/confirm-dialog'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import type { DeskAgentGateway } from '@/shared/deskagent'
import { getModelConfig, saveModelConfig } from '@/shared/deskagent/config'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Brain, Cpu, Eye, EyeOff, ImageIcon, Loader2, Mic, MonitorPlay, Plus, Volume2, X } from '@/shared/lib/icons'
import type { IconComponent } from '@/shared/lib/icons'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { ModelConfigPutRequest, ModelConfigResponse, ProviderSlotInput } from '@/shared/types/deskagent'

import { ListRow, LoadingState, SectionHeading, SettingsContent, SettingsSubsection } from './primitives'

// Mirrors backend KNOWN_PROVIDERS — the only provider names accepted by the
// provider-config slot resolution. Keep in sync with
// backend/services/llm/providers/registry.py.
const PROVIDER_OPTIONS = ['mimo', 'minimax', 'gemini', 'zhipu'] as const

// Single source of truth for all capability rendering, form mapping, and
// request-body construction. Adding a capability = one entry here.
const CAPABILITIES = [
  { key: 'llm', prefix: 'llm', icon: Brain, i18n: 'llm' },
  { key: 'stt', prefix: 'stt', icon: Mic, i18n: 'stt' },
  { key: 'tts', prefix: 'tts', icon: Volume2, i18n: 'tts' },
  { key: 'imageGen', prefix: 'image_gen', icon: ImageIcon, i18n: 'imageGen' },
  { key: 'videoGen', prefix: 'video_gen', icon: MonitorPlay, i18n: 'videoGen' }
] as const satisfies readonly { key: string; prefix: string; icon: IconComponent; i18n: string }[]

type CapabilityKey = (typeof CAPABILITIES)[number]['key']

interface CapabilityFormState {
  base_url: string
  api_key: string
  api_key_set: boolean
  api_key_fingerprint: string
  cleared_api_key: boolean
  model_name: string
}

type FormState = Record<CapabilityKey, CapabilityFormState>

const EMPTY_CAPABILITY: CapabilityFormState = {
  base_url: '',
  api_key: '',
  api_key_set: false,
  api_key_fingerprint: '',
  cleared_api_key: false,
  model_name: ''
}

function emptyForm(): FormState {
  return Object.fromEntries(CAPABILITIES.map(c => [c.key, { ...EMPTY_CAPABILITY }])) as FormState
}

function readForm(cfg: ModelConfigResponse): FormState {
  return Object.fromEntries(
    CAPABILITIES.map(c => [
      c.key,
      {
        base_url: cfg[`${c.prefix}_base_url` as keyof ModelConfigResponse] as string,
        api_key: '',
        api_key_set: cfg[`${c.prefix}_api_key_set` as keyof ModelConfigResponse] as boolean,
        api_key_fingerprint: c.key === 'llm' ? cfg.llm_api_key_fingerprint : '',
        cleared_api_key: false,
        model_name: cfg[`${c.prefix}_model_name` as keyof ModelConfigResponse] as string
      }
    ])
  ) as FormState
}

function buildPutBody(form: FormState, providers: ProviderSlotInput[]): ModelConfigPutRequest {
  const body: Record<string, unknown> = {}

  for (const c of CAPABILITIES) {
    const state = form[c.key as CapabilityKey]
    body[`${c.prefix}_base_url`] = state.base_url
    body[`${c.prefix}_api_key`] = state.cleared_api_key ? null : state.api_key
    body[`${c.prefix}_model_name`] = state.model_name
  }

  body.provider_config = providers

  return body as unknown as ModelConfigPutRequest
}

export function ModelConfigSettings({
  gateway,
  onConfigSaved
}: {
  gateway?: DeskAgentGateway | null
  onConfigSaved?: () => void
} = {}) {
  const t = strings
  const m = t.settings.models

  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [originalForm, setOriginalForm] = useState<FormState>(emptyForm)
  const [providers, setProviders] = useState<ProviderSlotInput[]>([])
  const [originalProviders, setOriginalProviders] = useState<ProviderSlotInput[]>([])
  const [clearingCap, setClearingCap] = useState<CapabilityKey | null>(null)
  const [clearAllOpen, setClearAllOpen] = useState(false)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const cfg = await getModelConfig()

        if (cancelled) {
          return
        }

        const next = readForm(cfg)
        setOriginalForm(next)
        setForm(next)
        setProviders(cfg.provider_config.map(s => ({ name: s.name, api_key: '', base_url: s.base_url })))
        setOriginalProviders(cfg.provider_config.map(s => ({ name: s.name, api_key: '', base_url: s.base_url })))
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

  // --- form helpers ---

  const updateCap = (cap: CapabilityKey, patch: Partial<CapabilityFormState>) => {
    setForm(prev => ({ ...prev, [cap]: { ...prev[cap], ...patch } }))
  }

  const onApiKeyChange = (cap: CapabilityKey, value: string) => {
    updateCap(cap, { api_key: value, cleared_api_key: value === '' ? form[cap].cleared_api_key : false })
  }

  const onClearApiKey = (cap: CapabilityKey) => {
    setClearingCap(cap)
  }

  const confirmClearApiKey = () => {
    if (clearingCap) {
      updateCap(clearingCap, { api_key: '', cleared_api_key: true })
    }
  }

  // --- dirty detection ---

  const isCapDirty = (cap: CapabilityKey): boolean => {
    const cur = form[cap]
    const orig = originalForm[cap]

    return (
      cur.cleared_api_key || cur.api_key !== '' || cur.base_url !== orig.base_url || cur.model_name !== orig.model_name
    )
  }

  const isProvidersDirty = (): boolean => {
    if (providers.length !== originalProviders.length) {
      return true
    }

    return providers.some((s, i) => {
      const orig = originalProviders[i]

      return !orig || s.name !== orig.name || s.base_url !== orig.base_url || s.api_key !== ''
    })
  }

  const isDirty = (Object.keys(form) as CapabilityKey[]).some(isCapDirty) || isProvidersDirty()

  // --- save ---

  const handleSave = async () => {
    try {
      setIsSaving(true)

      // PUT returns the updated public config — no second round-trip needed.
      const cfg = await saveModelConfig(buildPutBody(form, providers))
      const next = readForm(cfg)
      setOriginalForm(next)
      setForm(next)
      const nextProviders = cfg.provider_config.map(s => ({ name: s.name, api_key: '', base_url: s.base_url }))
      setProviders(nextProviders)
      setOriginalProviders(nextProviders)

      triggerHaptic('success')
      notify({ kind: 'success', title: m.heading, message: m.saved })

      // LLM config is frozen at WS connect time — reconnect to pick up changes.
      gateway?.close()

      onConfigSaved?.()
    } catch (err) {
      notifyError(err, m.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

  const handleClearAll = () => {
    const cleared = emptyForm()

    for (const c of CAPABILITIES) {
      cleared[c.key as CapabilityKey].cleared_api_key = originalForm[c.key as CapabilityKey].api_key_set
    }

    setForm(cleared)
    setProviders([])
  }

  // --- provider slots ---

  const addProviderSlot = () => {
    setProviders(prev => [...prev, { name: 'mimo', api_key: '', base_url: '' }])
  }

  const removeProviderSlot = (index: number) => {
    setProviders(prev => prev.filter((_, i) => i !== index))
  }

  const updateProviderSlot = (index: number, patch: Partial<ProviderSlotInput>) => {
    setProviders(prev => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)))
  }

  // --- render ---

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingState label={m.loading} />
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
              <Brain className="size-4" />
              <span>{m.heading}</span>
            </div>
          }
        />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <SectionHeading icon={Brain} title={m.heading} />

      <p className="mb-4 max-w-2xl text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
        {m.intro}
      </p>

      <InlineNotice kind="info">{m.reconnectNotice}</InlineNotice>

      {CAPABILITIES.map((cap, idx) => {
        const capKey = cap.key as CapabilityKey
        const capT = m.capabilities[cap.i18n as keyof typeof m.capabilities]

        return (
          <div key={capKey}>
            {idx > 0 ? <div className="my-4 h-px bg-border/30" /> : null}
            <CapabilitySection
              desc={capT.desc}
              disabled={isSaving}
              icon={cap.icon}
              onApiKeyChange={value => onApiKeyChange(capKey, value)}
              onClearApiKey={() => onClearApiKey(capKey)}
              state={form[capKey]}
              t={m}
              title={capT.title}
              updateBaseUrl={value => updateCap(capKey, { base_url: value })}
              updateModelName={value => updateCap(capKey, { model_name: value })}
            />
          </div>
        )
      })}

      <div className="my-4 h-px bg-border/30" />

      <SettingsSubsection icon={Cpu} intro={m.providers.intro} title={m.providers.heading}>
        {providers.length === 0 ? (
          <ListRow description={m.providers.empty} title="" />
        ) : (
          providers.map((slot, idx) => (
            <div className="space-y-2 rounded-md border border-border/30 p-3" key={idx}>
              <div className="flex items-center justify-between">
                <span className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                  {m.providers.name} #{idx + 1}
                </span>
                <Button disabled={isSaving} onClick={() => removeProviderSlot(idx)} size="sm" variant="ghost">
                  <X className="size-3.5" />
                  {m.providers.remove}
                </Button>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <Select
                  disabled={isSaving}
                  onValueChange={value => updateProviderSlot(idx, { name: value })}
                  value={slot.name}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PROVIDER_OPTIONS.map(opt => (
                      <SelectItem key={opt} value={opt}>
                        {opt}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  className="sm:col-span-1"
                  disabled={isSaving}
                  onChange={e => updateProviderSlot(idx, { base_url: e.currentTarget.value })}
                  placeholder={m.baseUrlPlaceholder}
                  value={slot.base_url}
                />
                <Input
                  className="sm:col-span-1"
                  disabled={isSaving}
                  onChange={e => updateProviderSlot(idx, { api_key: e.currentTarget.value })}
                  placeholder={m.apiKeyPlaceholder}
                  type="password"
                  value={slot.api_key}
                />
              </div>
            </div>
          ))
        )}
        <Button disabled={isSaving} onClick={addProviderSlot} size="sm" variant="outline">
          <Plus className="size-3.5" />
          {m.providers.addSlot}
        </Button>
      </SettingsSubsection>

      <div className="mt-8 flex items-center justify-between">
        <Button
          className="text-muted-foreground hover:text-destructive"
          disabled={isSaving}
          onClick={() => setClearAllOpen(true)}
          variant="ghost"
        >
          {m.clearAll}
        </Button>
        <Button disabled={isSaving || !isDirty} onClick={() => void handleSave()}>
          {isSaving ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {isSaving ? t.common.saving : t.common.save}
        </Button>
      </div>

      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={m.clearKey}
        description={m.clearKeyConfirm}
        onConfirm={confirmClearApiKey}
        onOpenChange={open => {
          if (!open) {
            setClearingCap(null)
          }
        }}
        open={clearingCap !== null}
        title={m.clearKey}
        variant="destructive"
      />
      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={m.clearAll}
        description={m.clearAllConfirm}
        onConfirm={handleClearAll}
        onOpenChange={setClearAllOpen}
        open={clearAllOpen}
        title={m.clearAll}
        variant="destructive"
      />
    </SettingsContent>
  )
}

function CapabilitySection({
  desc,
  disabled,
  icon: Icon,
  onApiKeyChange,
  onClearApiKey,
  state,
  t,
  title,
  updateBaseUrl,
  updateModelName
}: {
  desc: string
  disabled: boolean
  icon: IconComponent
  onApiKeyChange: (value: string) => void
  onClearApiKey: () => void
  state: CapabilityFormState
  t: (typeof strings)['settings']['models']
  title: string
  updateBaseUrl: (value: string) => void
  updateModelName: (value: string) => void
}) {
  const [revealed, setRevealed] = useState(false)
  const status = state.api_key_set ? t.set : t.notSet

  return (
    <SettingsSubsection icon={Icon} intro={desc} title={title}>
      <ListRow
        action={
          <Input
            className="max-w-sm"
            disabled={disabled}
            onChange={e => updateBaseUrl(e.currentTarget.value)}
            placeholder={t.baseUrlPlaceholder}
            value={state.base_url}
          />
        }
        title={t.baseUrl}
      />

      <ListRow
        action={
          <div className="flex items-center gap-2">
            <Input
              className="max-w-sm"
              disabled={disabled}
              onChange={e => onApiKeyChange(e.currentTarget.value)}
              placeholder={t.apiKeyPlaceholder}
              type={revealed ? 'text' : 'password'}
              value={state.api_key}
            />
            <Button
              aria-label={revealed ? t.hide : t.reveal}
              disabled={disabled}
              onClick={() => setRevealed(prev => !prev)}
              size="icon"
              type="button"
              variant="ghost"
            >
              {revealed ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </Button>
            {state.api_key_set ? (
              <Button
                aria-label={t.clearKey}
                disabled={disabled}
                onClick={onClearApiKey}
                size="icon"
                type="button"
                variant="ghost"
              >
                <X className="size-4" />
              </Button>
            ) : null}
          </div>
        }
        hint={state.api_key_set && state.api_key_fingerprint ? t.fingerprint(state.api_key_fingerprint) : undefined}
        title={
          <div className="flex items-center gap-2">
            <span>{t.apiKey}</span>
            <span className="text-[length:var(--conversation-caption-font-size)] font-normal text-(--ui-text-tertiary)">
              · {status}
            </span>
          </div>
        }
      />

      <ListRow
        action={
          <Input
            className="max-w-sm"
            disabled={disabled}
            onChange={e => updateModelName(e.currentTarget.value)}
            placeholder={t.modelNamePlaceholder}
            value={state.model_name}
          />
        }
        title={t.modelName}
      />
    </SettingsSubsection>
  )
}
