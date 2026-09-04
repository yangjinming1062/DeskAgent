import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

import { $renderMode } from '@/2d'
import { FloatingPanel } from '@/companion'
import { Palette, Shirt } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { type NavItemDescriptor, SettingsNav, SURFACE_CHROME } from '@/shared/panel'

import { $outfitView, type OutfitView } from './outfit-view'
import { AppearancePage } from './pages/appearance-page'
import { WardrobePage } from './pages/wardrobe-page'

// 换一身 / 形象独立入口面板：衣柜（2D）+ 形象（渲染模式 / 缩放）。
// 衣柜仅 2D 渲染模式可见；停留在衣柜时被切到 3D 则回落到「形象」页。
export function OutfitPanel({ onClose }: { onClose: () => void }): React.JSX.Element {
  const view = useStore($outfitView)
  const renderMode = useStore($renderMode)

  useEffect(() => {
    if (view === 'wardrobe' && renderMode !== '2d') {
      $outfitView.set('sprite-appearance')
    }
  }, [view, renderMode])

  const navItems: NavItemDescriptor[] = [
    { id: 'wardrobe', label: '衣柜', icon: Shirt },
    { id: 'sprite-appearance', label: '形象', icon: Palette }
  ].filter(item => item.id !== 'wardrobe' || renderMode === '2d') as NavItemDescriptor[]

  return (
    <FloatingPanel
      defaultSize={{ width: 960, height: 640 }}
      icon={Shirt}
      onClose={onClose}
      regionId="companion-outfit"
      static
      storagePrefix="da.companion.outfitPanel"
      title="换一身 / 形象"
    >
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            SURFACE_CHROME,
            'flex w-52 shrink-0 flex-col overflow-y-auto border-r border-line-standard p-2.5'
          )}
        >
          <SettingsNav activeId={view} items={navItems} onSelect={id => $outfitView.set(id as OutfitView)} />
        </aside>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {view === 'wardrobe' ? <WardrobePage /> : <AppearancePage />}
        </main>
      </div>
    </FloatingPanel>
  )
}
