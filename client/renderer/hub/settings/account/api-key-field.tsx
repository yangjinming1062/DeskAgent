import { SecretInputField } from '@/shared/components/ui'

import { ListRow } from '../primitives'

export type ApiKeyCopy = {
  set: string
  notSet: string
  fingerprint: (hash: string) => string
  reveal: string
  hide: string
  clearKey: string
}

export function ApiKeyField({
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
  copy: ApiKeyCopy
  description: string
  disabled: boolean
  fingerprint: string
  isSet: boolean
  onChange: (value: string) => void
  onClear: () => void
  placeholder: string
  title: string
  value: string
}): React.JSX.Element {
  const status = isSet ? copy.set : copy.notSet

  return (
    <ListRow
      action={
        <SecretInputField
          copy={copy}
          disabled={disabled}
          fingerprint={fingerprint}
          isSet={isSet}
          onChange={onChange}
          onClear={onClear}
          placeholder={placeholder}
          value={value}
        />
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
