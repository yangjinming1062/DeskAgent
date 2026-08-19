import { cva, type VariantProps } from 'class-variance-authority'

// 非编辑器类表单控件外观的唯一来源——Input、Textarea 与 SelectTrigger 都引用它。
// 与 `buttonVariants` 对齐：2.5px 圆角、12px 字号、按内边距决定尺寸（不设固定高度）。
// 视觉外观（背景、边框色调、悬停、聚焦光晕、非法态）由 `desktop-input-chrome` CSS
// 提供，保证所有控件外观完全一致。
export const controlVariants = cva(
  'desktop-input-chrome w-full min-w-0 rounded-[2.5px] border text-xs leading-4 text-foreground outline-none placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
  {
    variants: {
      size: {
        xs: 'px-2 py-0.5 text-[0.6875rem] leading-4',
        sm: 'px-2 py-1',
        default: 'px-2.5 py-1.5',
        lg: 'px-3 py-2 text-sm leading-5'
      }
    },
    defaultVariants: {
      size: 'default'
    }
  }
)

export type ControlVariantProps = VariantProps<typeof controlVariants>
