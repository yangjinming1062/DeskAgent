import { InlineNotice } from '@/shared/components/notifications'
import { cn } from '@/shared/lib/utils'
import { INPUT_CLASS, PanelSelect } from '@/shared/panel'
import type { strings } from '@/shared/strings'

import { ListRow, SettingsSubsection } from '../primitives'

import { ApiKeyField } from './api-key-field'

export type WebSearchCopy = (typeof strings)['settings']['account']['webSearch']

export interface WebFormState {
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

const WEB_BACKEND_OPTIONS = ['ddgs', 'brave-free', 'tavily'] as const
const EXTRACT_BACKEND_OPTIONS = ['tavily', 'brave-free', 'ddgs'] as const

function computeNotices(state: WebFormState, t: WebSearchCopy): string[] {
  const notices: string[] = []
  const extractIsTavily = state.extract_backend === 'tavily'
  const tavilyMissing = !state.tavily_api_key_set

  // 仅一条抽取相关提示；两个负面条件互斥，因为内部三元
  // 把 (extract_backend, key_set) 折叠为三种 key 之一或空。
  if (extractIsTavily && tavilyMissing) {
    notices.push(t.unavailable.extractTavilyNoKey)
  } else if (!extractIsTavily && tavilyMissing) {
    notices.push(t.unavailable.extractNonTavilyNoKey)
  } else if (!extractIsTavily) {
    notices.push(t.unavailable.extractNonTavilyWithKey)
  }

  // 搜索：仅当抽取提示尚未覆盖同一根因时，才显示 Tavily 缺少 key 的横幅——
  // 用户已经知道需要加 key。
  if (state.backend === 'brave-free' && !state.brave_api_key_set) {
    notices.push(t.unavailable.searchKeyFallback('Brave'))
  } else if (state.backend === 'tavily' && tavilyMissing && !notices.length) {
    notices.push(t.unavailable.searchKeyFallback('Tavily'))
  }

  return notices
}

export function WebSearchSection({
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
}): React.JSX.Element {
  return (
    <SettingsSubsection intro={t.intro} title={t.heading}>
      <ListRow
        action={
          <PanelSelect
            disabled={disabled}
            onChange={value => update({ backend: value })}
            options={WEB_BACKEND_OPTIONS.map(opt => ({ value: opt, label: t.backendOptions[opt] }))}
            value={state.backend}
            widthClass="w-44"
          />
        }
        description={t.backendDesc}
        title={t.backend}
      />

      <ListRow
        action={
          <PanelSelect
            disabled={disabled}
            onChange={value => update({ extract_backend: value })}
            options={EXTRACT_BACKEND_OPTIONS.map(opt => ({ value: opt, label: t.extractBackendOptions[opt] }))}
            value={state.extract_backend}
            widthClass="w-44"
          />
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
          <input
            className={cn(INPUT_CLASS, 'max-w-sm')}
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
