import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $spriteEmotion, $spriteState } from '../companion-store'

import { semanticRequestFor, WAITING_REQUEST } from './sprite-semantics'
import { $activeSprite, $staticMode, requestSprite } from './sprite-store'

// Degraded renderer for the no-GLB window (generating / failed / no key /
// swap gap). Rides above the 3D canvas inside SpriteStage: pointer events
// pass through to the stage's drag/poke handlers, and the canvas (egg) hides
// only while an image is actually on display — the egg stays the never-blank
// floor until the first sprite resolves. Crossfades between album images;
// breathing keeps it alive between switches (DESIGN.md §1.2).
const FADE_MS = 250

export function StaticSprite(): React.JSX.Element | null {
  const staticMode = useStore($staticMode)
  const activeSprite = useStore($activeSprite)
  const dataUrl = activeSprite?.dataUrl ?? null
  const prevUrlRef = useRef<string | null>(null)
  const [prevUrl, setPrevUrl] = useState<string | null>(null)

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

  if (!staticMode) {
    return null
  }

  return (
    <div aria-hidden="true" className="static-sprite-layer">
      {prevUrl && <img alt="" className="static-sprite-img" src={prevUrl} />}
      {dataUrl && <img alt="" className="static-sprite-img static-sprite-img--in" key={dataUrl} src={dataUrl} />}
    </div>
  )
}
