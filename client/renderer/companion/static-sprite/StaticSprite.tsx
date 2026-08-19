import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $spriteEmotion, $spriteState } from '../companion-store'

import { buildSpriteHitmap, type SpriteHit } from './sprite-hitmap'
import { semanticRequestFor, WAITING_REQUEST } from './sprite-semantics'
import { $activeSprite, $staticMode, requestSprite } from './sprite-store'

// 无 GLB 窗口时的降级渲染器（生成中 / 失败 / 无 key / 换模空挡）。
// 挂在 SpriteStage 内的 3D 画布之上：指针事件穿透给舞台的拖拽/戳击处理，
// 而画布（蛋兜底）只有在精灵图实际展示时才隐藏——首张精灵解析完成前
// 蛋仍是永不留白的底面。相册图之间做交叉淡入淡出；切换间隙靠呼吸动作维持
// 「活着」的状态（DESIGN.md §1.2）。
const FADE_MS = 250

interface StaticSpriteProps {
  onHitmapReady?: (hit: SpriteHit | null) => void
}

export function StaticSprite({ onHitmapReady }: StaticSpriteProps): React.JSX.Element | null {
  const staticMode = useStore($staticMode)
  const activeSprite = useStore($activeSprite)
  const dataUrl = activeSprite?.dataUrl ?? null
  const prevUrlRef = useRef<string | null>(null)
  const [prevUrl, setPrevUrl] = useState<string | null>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  useEffect(() => {
    if (!staticMode) {
      return
    }

    void requestSprite(WAITING_REQUEST, 'waiting')

    const fire = (): void => {
      void requestSprite(semanticRequestFor($spriteState.get(), $spriteEmotion.get()))
    }

    fire()

    const unsubState = $spriteState.listen(fire)
    const unsubEmotion = $spriteEmotion.listen(fire)

    return () => {
      unsubState()
      unsubEmotion()
    }
  }, [staticMode])

  useEffect(() => {
    if (dataUrl === prevUrlRef.current) {
      return
    }

    const old = prevUrlRef.current
    prevUrlRef.current = dataUrl

    if (dataUrl && old) {
      setPrevUrl(old)

      const timer = setTimeout(() => setPrevUrl(null), FADE_MS)

      return () => clearTimeout(timer)
    }

    setPrevUrl(null)
  }, [dataUrl])

  // hitmap 生命周期跟随挂载的 <img>：url 切换、退出静态模式、
  // 卸载时都让 hitmap 失效——过期的 hitmap 会采样已脱离 DOM 的元素的
  // 零尺寸矩形，让精灵变得点不到。
  useEffect(() => {
    if (!staticMode || !dataUrl) {
      onHitmapReady?.(null)

      return
    }

    return () => onHitmapReady?.(null)
  }, [staticMode, dataUrl, onHitmapReady])

  const onImgLoad = (): void => {
    const el = imgRef.current

    if (el?.complete && el.naturalWidth > 0) {
      const hitmap = buildSpriteHitmap(el)

      onHitmapReady?.(hitmap ? { el, hitmap } : null)
    }
  }

  if (!staticMode) {
    return null
  }

  return (
    <div aria-hidden="true" className="static-sprite-layer">
      {prevUrl && <img alt="" className="static-sprite-img" src={prevUrl} />}
      {dataUrl && (
        <img
          alt=""
          className="static-sprite-img static-sprite-img--in"
          key={dataUrl}
          onLoad={onImgLoad}
          ref={imgRef}
          src={dataUrl}
        />
      )}
    </div>
  )
}
