import type { ReactNode } from 'react'
import { useEffect, useRef } from 'react'

import { useInteractiveRegion } from '@/companion/interactive-regions'
import { PanelHeader } from '@/shared/panel'

interface WizardModalProps {
  regionId: string
  title: ReactNode
  onClose: () => void
  children: ReactNode
  // 固定在底部的操作条（上一步/下一步等），不随正文滚动。
  footer?: ReactNode
  // 内部还有自己的 Esc 语义（如灯箱打开时不关向导）时关闭默认 Esc 处理。
  escClose?: boolean
  widthClass?: string
}

// 伙伴窗线性向导的模态外壳：暗化背板 + 居中卡片。Esc 在捕获阶段拦截并阻断
// 冒泡——外层 FloatingPanel 的 Esc 处理器不会连坐关闭整个设置面板。
export function WizardModal({
  regionId,
  title,
  onClose,
  children,
  footer,
  escClose = true,
  widthClass = 'max-w-md'
}: WizardModalProps): React.JSX.Element {
  const overlayRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion(regionId, overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  useEffect(() => {
    if (!escClose) {
      return
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onClose()
      }
    }

    window.addEventListener('keydown', onKey, true)

    return () => window.removeEventListener('keydown', onKey, true)
  }, [escClose, onClose])

  return (
    <div
      className="pointer-events-auto fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      ref={overlayRef}
    >
      <div
        className={`flex max-h-[85vh] w-full ${widthClass} flex-col overflow-hidden rounded-2xl border border-white/12 bg-[#141416] text-white shadow-2xl`}
      >
        <PanelHeader onClose={onClose} title={title} />
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        {footer && <div className="flex items-center gap-2 border-t border-white/10 px-4 py-3">{footer}</div>}
      </div>
    </div>
  )
}
