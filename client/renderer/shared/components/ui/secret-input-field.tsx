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

// 由 account-settings.tsx（WebSearchCopy / API key）使用的"密码输入 + 显示 +
// 清除"三件套独立组件。调用方自行把它包进 ListRow 并加上标题与状态——这部分
// 差异较大（状态徽标位置、指纹提示格式等），不值得强行统一成单一组件形态。
export function SecretInputField({
  copy,
  value,
  onChange,
  isSet,
  fingerprint,
  onClear,
  placeholder,
  disabled
}: SecretInputFieldProps): React.JSX.Element {
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
      {/* 由调用方决定指纹提示放在哪里。指纹串通过 props 暴露、格式化函数通过 copy
          暴露，调用方的 ListRow 即可在合适的槽位渲染。 */}
      {isSet && fingerprint ? <span className="sr-only">{copy.fingerprint(fingerprint)}</span> : null}
    </div>
  )
}
