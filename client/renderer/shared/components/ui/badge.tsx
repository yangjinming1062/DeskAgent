import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'
import type * as React from 'react'

import { cn } from '@/shared/lib/utils'

// 小型状态 / 元信息标签。使用应用圆角（而非完整胶囊形）；配色映射到共享的
// 强调 / 弱化 / 警示底色，保证不同徽标视觉一致。
const badgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center gap-1 rounded-[3px] px-1.5 py-0.5 text-[0.65rem] font-medium leading-none whitespace-nowrap [&_svg]:size-3 [&_svg]:pointer-events-none',
  {
    variants: {
      variant: {
        default: 'bg-primary/10 text-primary',
        muted: 'bg-muted text-muted-foreground',
        warn: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
        destructive: 'bg-destructive/10 text-destructive',
        outline: 'border border-(--ui-stroke-secondary) text-muted-foreground'
      }
    },
    defaultVariants: { variant: 'default' }
  }
)

export interface BadgeProps extends React.ComponentProps<'span'>, VariantProps<typeof badgeVariants> {
  asChild?: boolean
}

export function Badge({ asChild = false, className, variant, ...props }: BadgeProps): React.JSX.Element {
  const Comp = asChild ? Slot.Root : 'span'

  return <Comp className={cn(badgeVariants({ variant }), className)} data-slot="badge" {...props} />
}

export { badgeVariants }
