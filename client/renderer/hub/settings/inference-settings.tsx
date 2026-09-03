import { useEffect, useState } from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { Brain } from '@/shared/lib/icons'
import { BTN_PRIMARY, LoadingBlock, Spinner } from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'
import type { SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

import { AgentDefaultsSection, type AgentFormState } from './inference/agent-defaults-section'
import { type ChatFormState, ContextCompressionSection } from './inference/context-compression-section'
import { type TemperatureFormState, TemperatureSection } from './inference/temperature-section'
import { ListRow, SectionHeading, SettingsContent } from './primitives'
import { useFormSection } from './use-form-section'

const REASONING_OPTIONS = ['none', 'low', 'medium', 'high'] as const

const EMPTY_AGENT: AgentFormState = {
  reasoning_effort: 'low',
  enable_background_review: true
}

const EMPTY_CHAT: ChatFormState = {
  enable_context_compression: true,
  context_compression_threshold: 0.7
}

const EMPTY_TEMPERATURE: TemperatureFormState = {
  chat_temperature: 0.7,
  title_generation_temperature: 0.3,
  compression_temperature: 0.0
}

const readAgentState = (config: SpiritAgentConfigResponse): AgentFormState => {
  const agent = config.agent

  return {
    reasoning_effort:
      REASONING_OPTIONS.find(option => option === agent?.reasoning_effort) ?? EMPTY_AGENT.reasoning_effort,
    enable_background_review: agent?.enable_background_review ?? EMPTY_AGENT.enable_background_review
  }
}

const readChatState = (config: SpiritAgentConfigResponse): ChatFormState => ({
  enable_context_compression: config.chat?.enable_context_compression ?? EMPTY_CHAT.enable_context_compression,
  context_compression_threshold: config.chat?.context_compression_threshold ?? EMPTY_CHAT.context_compression_threshold
})

const readTemperatureState = (config: SpiritAgentConfigResponse): TemperatureFormState => ({
  chat_temperature: config.agent?.temperature ?? EMPTY_TEMPERATURE.chat_temperature,
  title_generation_temperature:
    config.chat?.title_generation_temperature ?? EMPTY_TEMPERATURE.title_generation_temperature,
  compression_temperature: config.chat?.compression_temperature ?? EMPTY_TEMPERATURE.compression_temperature
})

export function InferenceSettings(): React.JSX.Element {
  const t = strings
  const a = t.settings.inference

  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const agent = useFormSection(EMPTY_AGENT, readAgentState)
  const chat = useFormSection(EMPTY_CHAT, readChatState)
  const temperature = useFormSection(EMPTY_TEMPERATURE, readTemperatureState)

  const { reset: resetAgent } = agent
  const { reset: resetChat } = chat
  const { reset: resetTemperature } = temperature

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const config = await getSpiritAgentConfig()

        if (cancelled) {
          return
        }

        resetAgent(config)
        resetChat(config)
        resetTemperature(config)
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
  }, [resetAgent, resetChat, resetTemperature])

  const isDirty = agent.isDirty || chat.isDirty || temperature.isDirty

  const handleSave = async () => {
    try {
      setIsSaving(true)

      const { config } = await saveSpiritAgentConfig({
        agent: {
          enable_background_review: agent.state.enable_background_review,
          reasoning_effort: agent.state.reasoning_effort,
          temperature: temperature.state.chat_temperature
        },
        chat: {
          enable_context_compression: chat.state.enable_context_compression,
          context_compression_threshold: chat.state.context_compression_threshold,
          title_generation_temperature: temperature.state.title_generation_temperature,
          compression_temperature: temperature.state.compression_temperature
        }
      })

      agent.reset(config)
      chat.reset(config)
      temperature.reset(config)
      triggerHaptic('success')
      notify({ kind: 'success', title: a.heading, message: a.saved })
    } catch (err) {
      notifyError(err, a.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <SettingsContent>
        <LoadingBlock label={a.loading} />
      </SettingsContent>
    )
  }

  if (loadError) {
    return (
      <SettingsContent>
        <ListRow
          description={loadError}
          title={
            <div className="flex items-center gap-2 text-rose-300/80">
              <Brain className="size-4" />
              <span>{a.heading}</span>
            </div>
          }
        />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <SectionHeading title={a.heading} />

      <AgentDefaultsSection disabled={isSaving} state={agent.state} t={a.agentDefaults} update={agent.set} />

      <div className="my-4 h-px bg-line-standard" />

      <ContextCompressionSection disabled={isSaving} state={chat.state} t={a.contextCompression} update={chat.set} />

      <div className="my-4 h-px bg-line-standard" />

      <TemperatureSection disabled={isSaving} state={temperature.state} t={a.temperature} update={temperature.set} />

      <div className="mt-8 flex justify-end">
        <button className={BTN_PRIMARY} disabled={isSaving || !isDirty} onClick={() => void handleSave()} type="button">
          {isSaving && <Spinner className="size-3.5" />}
          {isSaving ? t.common.saving : t.common.save}
        </button>
      </div>
    </SettingsContent>
  )
}
