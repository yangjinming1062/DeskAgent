'use client'

import { useState } from 'react'

import { Eye, EyeOff, X } from '@/shared/lib/icons'

import { Button } from './button'
import { Input } from './input'

interface SecretInputFieldCopy {
  /** i18n key — copy when the secret is currently stored. */
  set: string
  /** i18n key — copy when the secret is not yet stored. */
  notSet: string
  /** aria-label for the reveal toggle (currently hidden). */
  reveal: string
  /** aria-label for the reveal toggle (currently shown). */
  hide: string
  /** aria-label for the clear button. */
  clearKey: string
  /** Renders the fingerprint line under the input. */
  fingerprint: (fp: string) => string
}

interface SecretInputFieldProps {
  copy: SecretInputFieldCopy
  value: string
  onChange: (next: string) => void
  isSet: boolean
  fingerprint?: string
  onClear?: () => void
  placeholder?: string
  disabled?: boolean
}

// Standalone "password input + reveal + clear" trio used by both
// account-settings.tsx (WebSearchCopy / API keys) and
// model-config-settings.tsx (CapabilitySection api-key row). Callers wrap
// it in a ListRow with title + status themselves — that part differs
// (status pill position, fingerprint hint format, etc.) and is not
// worth forcing into a single component shape.
export function SecretInputField({
  copy,
  value,
  onChange,
  isSet,
  fingerprint,
  onClear,
  placeholder,
  disabled
}: SecretInputFieldProps) {
  const [revealed, setRevealed] = useState(false)

  return (
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
      {isSet && onClear ? (
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
      {/* Caller decides where the fingerprint hint lives. We expose the
          fingerprint string via props and the formatter via copy so the
          caller's ListRow can render it in the right slot. */}
      {isSet && fingerprint ? <span className="sr-only">{copy.fingerprint(fingerprint)}</span> : null}
    </div>
  )
}
