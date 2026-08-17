import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $spriteEmotion, $spriteState } from '../companion-store'

import { buildSpriteHitmap, type SpriteHit } from './sprite-hitmap'
import { semanticRequestFor, WAITING_REQUEST } from './sprite-semantics'
import { $activeSprite, $staticMode, requestSprite } from './sprite-store'

// Degraded renderer for the no-GLB window (generating / failed / no key /
// swap gap). Rides above the 3D canvas inside SpriteStage: pointer events
// pass through to the stage's drag/poke handlers, and the canvas (egg) hides
// only while an image is actually on display — the egg stays the never-blank
// floor until the first sprite resolves. Crossfades between album images;
// breathing keeps it alive between switches (DESIGN.md §1.2).
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

  // Hitmap lifetime tracks the mounted <img>: invalidated on url swap,
  // static-mode exit and unmount — a stale hitmap would sample a detached
  // element's zero rect and make the sprite unhittable.
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
