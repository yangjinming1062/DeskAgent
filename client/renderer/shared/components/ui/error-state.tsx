import type { ReactNode } from 'react'

import { cn } from '@/shared/lib/utils'

import { Codicon } from './codicon'

export function ErrorIcon({ className, size = '1.75rem' }: { className?: string; size?: string }): React.JSX.Element {
  return <Codicon className={cn('text-destructive', className)} name="error" size={size} />
}

export interface ErrorStateProps {
  /** Optional actions row/stack rendered below the copy. */
  children?: ReactNode
  className?: string
  description?: ReactNode
  /** Defaults to a destructive AlertCircle. */
  icon?: ReactNode
  title: ReactNode
}

export function ErrorState({ children, className, description, icon, title }: ErrorStateProps): React.JSX.Element {
  return (
    <div className={cn('grid gap-5', className)}>
      <div className="flex flex-col items-center gap-3 text-center">
        {icon ?? <ErrorIcon />}

        {typeof title === 'string' ? (
          <h2 className="text-center text-xl font-semibold tracking-tight">{title}</h2>
        ) : (
          title
        )}

        {typeof description === 'string' ? (
          <p className="max-w-prose text-center text-sm leading-5 text-muted-foreground">{description}</p>
        ) : (
          description
        )}
      </div>

      {children && <div className="grid gap-2">{children}</div>}
    </div>
  )
}
