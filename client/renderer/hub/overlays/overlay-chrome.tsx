import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cn } from '@/shared/lib/utils'

type OverlayTone = 'subtle'

interface OverlayActionButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: OverlayTone
}

function OverlayActionButton({
  children,
  className,
  tone = 'subtle',
  type = 'button',
  ...props
}: OverlayActionButtonProps): React.JSX.Element {
  return (
    <button
      className={cn(
        'inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium transition-colors disabled:cursor-default disabled:opacity-45',
        tone === 'subtle' &&
          'h-7 border-transparent px-2 text-muted-foreground hover:border-[color-mix(in_srgb,var(--dt-border)_54%,transparent)] hover:bg-[color-mix(in_srgb,var(--dt-card)_72%,transparent)] hover:text-foreground',
        className
      )}
      type={type}
      {...props}
    >
      {children}
    </button>
  )
}

interface OverlayIconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode
}

export function OverlayIconButton({
  children,
  className,
  type = 'button',
  ...props
}: OverlayIconButtonProps): React.JSX.Element {
  return (
    <OverlayActionButton
      className={cn('h-7 w-7 justify-center px-0 [&_svg]:size-4', className)}
      tone="subtle"
      type={type}
      {...props}
    >
      {children}
    </OverlayActionButton>
  )
}
