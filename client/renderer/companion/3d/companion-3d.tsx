import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { registerAmplitudeSink } from '@/companion/audio-track'
import { $chatOpen } from '@/companion/chat-store'
import { $spriteEmotion, $spriteState, type SpriteEmotion, type SpriteStateName } from '@/companion/companion-store'

import { Engine } from './Engine'
import { $equippedItem, $modelInfo, refreshEquippedAndApply } from './model-store'

// Mounts the Three.js engine into the sprite-stage canvas. The sprite-stage
// owns drag / click-through / region tracking; this component only renders.
//
// - Subscribes to $spriteState / $spriteEmotion and forwards them to the
//   character controller for animation + morph playback.
// - Reacts to $modelInfo.asset_url changes by reloading the GLB; first load
//   fires via a mount effect so the GLB fetch is cancelled on unmount.
// - TTS lip-sync: hooks the audio-track AnalyserNode via tts-bridge.
// - Look-at: tracks the cursor over the canvas when the chat dock is closed.
// - Outfit: applies the currently equipped wardrobe item whenever the
//   equipped atom changes (initial mount + hot-swap).

interface CharacterSnapshot {
  state: SpriteStateName
  emotion: SpriteEmotion | null
}

function captureSpriteSnapshot(): CharacterSnapshot {
  return { state: $spriteState.get(), emotion: $spriteEmotion.get() }
}

export function Companion3D(): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const engineRef = useRef<Engine | null>(null)
  const equipped = useStore($equippedItem)
  const modelInfo = useStore($modelInfo)

  // Mount engine, wire subscriptions, and kick off initial model load.
  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    const engine = new Engine({
      canvas,
      width: canvas.clientWidth || 320,
      height: canvas.clientHeight || 320
    })

    engineRef.current = engine

    const initial = captureSpriteSnapshot()
    engine.character.applyState(initial.state, initial.emotion)

    const unsubState = $spriteState.listen(state => {
      engine.character.applyState(state, $spriteEmotion.get())
    })

    const unsubEmotion = $spriteEmotion.listen(emotion => {
      engine.character.applyState($spriteState.get(), emotion)
    })

    // TTS lip-sync — the audio-track AnalyserNode pushes amplitude every frame
    // while audio is playing; we just forward it.
    const detachLipSync = registerAmplitudeSink(amp => engine.character.setLipSyncAmplitude(amp))

    const onResize = () => {
      const w = canvas.clientWidth || window.innerWidth
      const h = canvas.clientHeight || window.innerHeight
      engine.resize(w, h)
    }

    const ro = new ResizeObserver(onResize)
    ro.observe(canvas)
    window.addEventListener('resize', onResize)

    // Look-at — only when chat is closed (avoid head twitching while typing).
    const onPointerMove = (e: PointerEvent) => {
      if ($chatOpen.get()) {
        return
      }

      const rect = canvas.getBoundingClientRect()
      const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1
      const ny = ((e.clientY - rect.top) / rect.height) * 2 - 1
      engine.character.setLookTarget(nx, ny)
    }

    canvas.addEventListener('pointermove', onPointerMove)

    engine.start()

    return () => {
      unsubState()
      unsubEmotion()
      detachLipSync()
      window.removeEventListener('resize', onResize)
      ro.disconnect()
      canvas.removeEventListener('pointermove', onPointerMove)
      engine.dispose()
      engineRef.current = null
    }
  }, [])

  // Load (or reload) the GLB whenever the model's asset URL changes.
  useEffect(() => {
    const engine = engineRef.current

    if (!engine) {
      return
    }

    let cancelled = false
    void engine
      .loadCharacter(modelInfo.asset_url)
      .then(() => {
        if (cancelled) {
          return
        }

        // Re-apply morph params + the currently equipped outfit so reloads
        // preserve the user's customisation.
        if (Object.keys(modelInfo.morph_params).length) {
          engine.character.setMorphs(modelInfo.morph_params)
        }

        refreshEquippedAndApply()
      })
      .catch(err => {
        if (cancelled) {
          return
        }

        console.error('[companion-3d] loadCharacter failed:', err)
      })

    return () => {
      cancelled = true
    }
  }, [modelInfo.asset_url, modelInfo.morph_params])

  // Apply equipped outfit on every change. setOutfit is a no-op when the
  // character is the procedural fallback (no GLB materials to edit).
  useEffect(() => {
    const engine = engineRef.current

    if (!engine) {
      return
    }

    if (equipped) {
      engine.character.setOutfit(equipped)
    }
  }, [equipped])

  return <canvas className="companion-3d-canvas" ref={canvasRef} />
}
