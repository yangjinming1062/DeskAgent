import * as RadixUI from 'radix-ui'
import * as React from 'react'

import { cn } from '@/shared/lib/utils'

const TooltipPrimitive = RadixUI.Tooltip

function TooltipProvider({
  delayDuration = 0,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>): React.JSX.Element {
  return <TooltipPrimitive.Provider data-slot="tooltip-provider" delayDuration={delayDuration} {...props} />
}

function Tooltip({ ...props }: React.ComponentProps<typeof TooltipPrimitive.Root>): React.JSX.Element {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

function TooltipTrigger({ ...props }: React.ComponentProps<typeof TooltipPrimitive.Trigger>): React.JSX.Element {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />
}

function TooltipContent({
  className,
  sideOffset = 6,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>): React.JSX.Element {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        // 即时显示、无过渡动画（Provider 的 delayDuration=0，且不使用 animate-* 类）。
        // bg-foreground / text-background 按主题自动反色：浅色主题为黑底白字，
        // 深色主题为白底黑字。
        className={cn(
          'z-[200] w-fit bg-foreground px-1.5 py-1 text-[11px] font-bold leading-none text-background select-none [font-family:Arial,sans-serif]',
          className
        )}
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        {...props}
      >
        {children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  )
}

interface TipProps extends Omit<React.ComponentProps<typeof TooltipPrimitive.Content>, 'content'> {
  label: React.ReactNode
  children: React.ReactNode
  delayDuration?: number
}

// 原生 `title=` 的即插即用替代：包住任意单个元素。即时显示、自动避让、
// 主题化。自带 Provider，无需上层 Provider 包装。label 为空时直接渲染子元素。
function Tip({ label, children, delayDuration = 0, ...props }: TipProps): React.JSX.Element {
  if (!label) {
    return <>{children}</>
  }

  return (
    <TooltipProvider delayDuration={delayDuration}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent {...props}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export { Tip, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
