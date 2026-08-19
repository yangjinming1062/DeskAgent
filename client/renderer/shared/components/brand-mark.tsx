import { cn } from '@/shared/lib/utils'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

// 品牌徽标：SpiritAgent 应用图标，浅色 / 深色下保持一致。
// 图标是透明 PNG，无需底色块；尺寸通过 className 控制（默认 size-14）。
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>): React.JSX.Element {
  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img alt="" className="size-full object-contain" src={assetPath('icon.png')} />
    </span>
  )
}
