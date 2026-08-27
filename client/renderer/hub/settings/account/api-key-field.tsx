import { SecretInput } from '@/shared/panel'

import { ListRow } from '../primitives'

type ApiKeyCopy = {
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
        <SecretInput
          clearLabel={copy.clearKey}
          disabled={disabled}
          hideLabel={copy.hide}
          isSet={isSet}
          onChange={onChange}
          onClear={onClear}
          placeholder={placeholder}
          revealLabel={copy.reveal}
          value={value}
        />
      }
      description={description}
      hint={isSet ? copy.fingerprint(fingerprint) : undefined}
      title={
        <div className="flex items-center gap-2">
          <span>{title}</span>
          <span className="text-[10px] font-normal text-white/40">· {status}</span>
        </div>
      }
    />
  )
}
