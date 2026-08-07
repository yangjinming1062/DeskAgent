import { useEffect, useRef } from 'react'
import { Engine } from '../engine/Engine'
import { $spriteEmotion, $spriteState } from '../state/companion-store'
import { setAmplitudeCallback } from '../tts'
import { $chatOpen } from '../state/chat-store'
import { refreshEquippedAndApply, setApplyOutfitFn, type WardrobeItem } from '../state/wardrobe-store'
import { getApiClient } from '../state/auth-store'

const DRAG_THRESHOLD = 12

export function CompanionCanvas({ modelUrl }: { modelUrl: string | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const engineRef = useRef<Engine | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = new Engine({ canvas, width: window.innerWidth, height: window.innerHeight })
    engineRef.current = engine

    const unsubState = $spriteState.listen(s => engine.character.applyState(s, $spriteEmotion.get()))
    const unsubEmotion = $spriteEmotion.listen(e => engine.character.applyState($spriteState.get(), e))
    setAmplitudeCallback(amp => engine.character.setLipSyncAmplitude(amp))
    setApplyOutfitFn((item: WardrobeItem) => engine.character.setOutfit(item))
    engine.start()

    const onResize = () => engine.resize(window.innerWidth, window.innerHeight)
    window.addEventListener('resize', onResize)

    // Input: click toggles chat, drag moves character, mouse move drives look-at
    let downX = 0, downY = 0, charX = 0, dragging = false

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0) return
      downX = e.clientX; downY = e.clientY
      charX = engine.character.root.position.x
      dragging = false
      canvas.setPointerCapture(e.pointerId)
    }
    const onPointerMove = (e: PointerEvent) => {
      // Look-at only when chat is closed — don't have the companion's head
      // twitch while the user is reading or typing.
      if (!$chatOpen.get()) {
        const nx = (e.clientX / window.innerWidth) * 2 - 1
        const ny = (e.clientY / window.innerHeight) * 2 - 1
        engine.character.setLookTarget(nx, ny)
      }
      if (e.buttons & 1) {
        const dx = e.clientX - downX
        if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(e.clientY - downY) > DRAG_THRESHOLD) dragging = true
        if (dragging) {
          engine.character.root.position.x = Math.max(-2, Math.min(2, charX + (dx / window.innerWidth) * 4))
        }
      }
    }
    const onPointerUp = (e: PointerEvent) => {
      if (e.button !== 0) return
      canvas.releasePointerCapture(e.pointerId)
      if (!dragging) $chatOpen.set(!$chatOpen.get())
    }

    canvas.addEventListener('pointerdown', onPointerDown)
    canvas.addEventListener('pointermove', onPointerMove)
    canvas.addEventListener('pointerup', onPointerUp)

    return () => {
      unsubState()
      unsubEmotion()
      setAmplitudeCallback(null)
      setApplyOutfitFn(null)
      window.removeEventListener('resize', onResize)
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      engine.dispose()
      engineRef.current = null
    }
  }, [])

  // Reload model when URL changes (also handles the initial mount)
  useEffect(() => {
    if (!engineRef.current) return
    let cancelled = false
    engineRef.current.loadCharacter(modelUrl).then(() => {
      if (cancelled) return
      void refreshEquippedAndApply(getApiClient())
    })
    return () => { cancelled = true }
  }, [modelUrl])

  return <canvas ref={canvasRef} id="canvas" />
}
