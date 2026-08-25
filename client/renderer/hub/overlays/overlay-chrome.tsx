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
        tone === 'subtle' && 'h-7 border-transparent px-2 text-white/50 hover:bg-white/10 hover:text-white',
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
