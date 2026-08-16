import { cn } from '@/shared/lib/utils'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

// Brand badge: SpiritAgent app icon, identical in light/dark. Icon is a transparent PNG
// so no tile bg is needed; size via className (default size-14).
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>): React.JSX.Element {
  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img alt="" className="size-full object-contain" src={assetPath('icon.png')} />
    </span>
  )
}
