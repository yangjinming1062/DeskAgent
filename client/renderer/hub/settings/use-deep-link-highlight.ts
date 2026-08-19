import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'

interface DeepLinkHighlightOptions {
  param: string
  ready: (target: string) => boolean
  elementId: (target: string) => string
  onResolve?: (target: string) => void
  block?: ScrollLogicalPosition
}

// 命令面板的深度链接（?<param>=<id>）：目标行可渲染后，
// 滚入视口并高亮闪烁，然后移除该参数以避免重复触发。
// 返回待处理目标（消费后为 null），让调用方能在挂载前强行打开该行。
export function useDeepLinkHighlight({
  param,
  ready,
  elementId,
  onResolve,
  block = 'center'
}: DeepLinkHighlightOptions): null | string {
  const [searchParams, setSearchParams] = useSearchParams()
  const target = searchParams.get(param)

  useEffect(() => {
    if (!target || !ready(target)) {
      return
    }

    onResolve?.(target)

    // 延迟一帧，让异步状态（展开、选中）先挂载该行。
    const scrollTimeout = window.setTimeout(() => {
      const element = document.getElementById(elementId(target))

      if (!element) {
        return
      }

      element.scrollIntoView({ behavior: 'smooth', block })
      element.classList.add('setting-field-highlight')
      window.setTimeout(() => element.classList.remove('setting-field-highlight'), 1600)
    }, 80)

    setSearchParams(
      previous => {
        const next = new URLSearchParams(previous)
        next.delete(param)

        return next
      },
      { replace: true }
    )

    return () => window.clearTimeout(scrollTimeout)
  }, [block, elementId, onResolve, param, ready, setSearchParams, target])

  return target
}
