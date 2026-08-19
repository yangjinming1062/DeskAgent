import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'
import * as React from 'react'

import { cn } from '@/shared/lib/utils'

// 文本按钮为方形（无圆角），尺寸由内边距 + 行高决定——不设固定高度——
// 因此可以紧贴内容并随内容缩放。只有图标按钮（天然方形）才使用共享的 4px 圆角。
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[2.5px] text-xs leading-4 font-medium whitespace-nowrap shadow-none transition-all duration-100 outline-none focus-visible:border-ring focus-visible:ring-[0.1875rem] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-default disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        destructive:
          'bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40',
        // 弱化动作——透明背景加 1.5px 内描边（不引入位移的边框）。
        outline:
          'bg-transparent text-(--ui-text-primary) shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--ui-stroke-secondary)_50%,transparent)] hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        // 浅填充动作（默认的"非主按钮"外观）。
        secondary:
          'bg-(--ui-bg-quaternary) text-(--ui-text-primary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        ghost: 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        link: 'text-primary underline-offset-4 decoration-current/20 hover:underline',
        // 无框行内文本动作（无背景 / 无边框）。默认弱化呈现——看起来像弱化的
        // 标签文字，悬停时加下划线（如"取消"、"清除"）。
        text: 'text-muted-foreground underline-offset-4 hover:text-foreground hover:underline',
        // 强调型行内文本动作：加粗 + 始终带下划线的链接。用于一行内需要强调的
        // 可点入口（"修改"、"设置"、"打开日志" 等）。
        textStrong: 'font-semibold text-muted-foreground underline underline-offset-4 hover:text-foreground'
      },
      size: {
        default: 'px-3 py-1.5 has-[>svg]:px-2.5',
        xs: "gap-1 px-2 py-0.5 text-[0.6875rem] leading-4 has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: 'px-2.5 py-1 has-[>svg]:px-2',
        lg: 'px-5 py-2 text-sm leading-5 has-[>svg]:px-4',
        // 紧凑行内文本动作——无外框内边距 / 高度。当按钮需要嵌入标题或句中时，
        // 配合 text / link 变体使用（替代临时拼凑的 `h-auto px-0 py-0`）。
        inline: 'h-auto gap-1 p-0 has-[>svg]:px-0',
        icon: 'size-9 rounded-[4px]',
        'icon-xs': "size-6 rounded-[4px] [&_svg:not([class*='size-'])]:size-3",
        'icon-sm': 'size-8 rounded-[4px]',
        'icon-lg': 'size-10 rounded-[4px]',
        'icon-titlebar':
          'h-(--titlebar-control-height) w-(--titlebar-control-size) rounded-[4px] [&_.codicon]:text-[0.875rem]'
      }
    },
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
)

function Button({
  className,
  variant = 'default',
  size = 'default',
  asChild = false,
  ...props
}: React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }): React.JSX.Element {
  const Comp = asChild ? Slot.Root : 'button'

  return (
    <Comp
      className={cn(buttonVariants({ variant, size }), className)}
      data-size={size}
      data-slot="button"
      data-variant={variant}
      {...props}
    />
  )
}

export { Button, buttonVariants }
