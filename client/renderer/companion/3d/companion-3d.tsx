import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { registerAmplitudeSink } from '@/companion/audio-track'
import { $chatOpen } from '@/companion/chat-store'
import {
  $clipOverride,
  $spriteAction,
  $spriteEmotion,
  $spriteState,
  type SpriteEmotion,
  type SpriteStateName
} from '@/companion/companion-store'
import { probeInteractiveRegions, useInteractiveRegion } from '@/companion/interactive-regions'
import { $personalityTags } from '@/companion/persona-store'
import { $activeSprite, $glbLoadFailed, $staticMode } from '@/companion/static-sprite/sprite-store'
import { log } from '@/shared/lib/log'

import { $dragVelocity, $spatialLocomotion, getBaseSpriteHeight, getBaseSpriteWidth } from '../spatial'
import { $contextMenuOpen } from '../sprite/context-menu-store'

import { Engine } from './Engine'
import { fetchGlbWithCache } from './glb-opfs-cache'
import {
  $expressions,
  $generatedClips,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  $modelInfo,
  $modelLoadSettled,
  $modelRetryable,
  $modelRetryModelId,
  $outfitView,
  $renderStyle,
  hydrateExpressions,
  hydrateGeneratedClips,
  refreshEquippedAndApply,
  retryModelDownload
} from './model-store'
import { subscribePowerProfile } from './power-signals'
import { attachSilhouetteHitProbe } from './silhouette-hit'

// Mounts the Three.js engine into the sprite-stage canvas. The sprite-stage
// owns drag / click-through / region tracking; this component only renders.
//
// - Engine.create is async (WebGPU init) — the ready promise is shared with
//   the load/outfit effects below so they never race the boot; a null
//   resolution means the component unmounted mid-init.
// - Subscribes to $spriteState / $spriteEmotion and forwards them to the
//   character controller for animation + morph playback. Snapshots are taken
//   at engine-ready time (post-await), so state flips during the async boot
//   window are not lost.
// - Reacts to $modelInfo.asset_url changes by reloading the GLB.
// - TTS lip-sync: hooks the audio-track AnalyserNode via tts-bridge.
// - Look-at: tracks the cursor over the canvas when the chat dock is closed.
// - Outfit: applies the currently equipped wardrobe item whenever the
//   equipped atom changes (initial mount + hot-swap).

interface CharacterSnapshot {
  state: SpriteStateName
  emotion: SpriteEmotion | null
  action: string | null
}

function captureSpriteSnapshot(): CharacterSnapshot {
  return { state: $spriteState.get(), emotion: $spriteEmotion.get(), action: $spriteAction.get() }
}

export function Companion3D(): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const engineReadyRef = useRef<Promise<Engine | null> | null>(null)
  const outfitView = useStore($outfitView)
  const modelInfo = useStore($modelInfo)
  const renderStyle = useStore($renderStyle)

  // Boot engine, wire subscriptions, and kick off initial model load.
  useEffect(() => {
    const container = containerRef.current

    if (!container) {
      return
    }

    let cancelled = false
    let engine: Engine | null = null
    let detachWiring: (() => void) | null = null

    const ready = Engine.create({ container }).then(created => {
      if (cancelled) {
        created.dispose()

        return null
      }

      const eng = created
      engine = created

      const initial = captureSpriteSnapshot()
      eng.character.applyState(initial.state, initial.emotion, {
        companionTags: $personalityTags.get(),
        action: initial.action
      })

      const initialGenerated = $generatedClips.get()

      if (initialGenerated.length > 0) {
        eng.character.appendClipDefs(initialGenerated)
      }

      const unsubState = $spriteState.listen(state => {
        const tags = $personalityTags.get()
        const override = state === 'interacting' ? $clipOverride.get() : undefined
        eng.character.applyState(state, $spriteEmotion.get(), {
          companionTags: tags,
          clipOverride: override,
          action: $spriteAction.get()
        })

        if (state !== 'interacting') {
          $clipOverride.set(null)
        }
      })

      const unsubEmotion = $spriteEmotion.listen(emotion => {
        eng.character.applyState($spriteState.get(), emotion, {
          companionTags: $personalityTags.get(),
          customExpressions: $expressions.get(),
          action: $spriteAction.get()
        })
      })

      const unsubGenerated = $generatedClips.listen(clips => {
        if (clips.length > 0) {
          eng.character.appendClipDefs(clips)
        }
      })

      // TTS lip-sync — the audio-track AnalyserNode pushes amplitude every frame
      // while audio is playing; we just forward it.
      const detachLipSync = registerAmplitudeSink(amp => eng.character.setLipSyncAmplitude(amp))

      let cachedW = getBaseSpriteWidth()
      let cachedH = getBaseSpriteHeight()

      const onResize = () => {
        const container = containerRef.current
        cachedW = container?.clientWidth || eng.canvas.clientWidth || getBaseSpriteWidth()
        cachedH = container?.clientHeight || eng.canvas.clientHeight || getBaseSpriteHeight()
        eng.resize(cachedW, cachedH)
      }

      const ro = new ResizeObserver(onResize)

      if (containerRef.current) {
        ro.observe(containerRef.current)
      } else {
        ro.observe(eng.canvas)
      }

      window.addEventListener('resize', onResize)

      // Look-at — smoothly track cursor when hovering over the companion without triggering DOM reflows
      const onPointerMove = (e: PointerEvent) => {
        if ($chatOpen.get() || $spatialLocomotion.get() === 'drag' || $contextMenuOpen.get()) {
          eng.character.setLookTarget(0, 0)

          return
        }

        const nx = (e.offsetX / (cachedW || 1)) * 2 - 1
        const ny = (e.offsetY / (cachedH || 1)) * 2 - 1
        eng.character.setLookTarget(nx, ny)
      }

      const onPointerLeave = () => {
        eng.character.setLookTarget(0, 0)
      }

      eng.canvas.addEventListener('pointermove', onPointerMove)
      eng.canvas.addEventListener('pointerleave', onPointerLeave)

      // Render-power tiers — resolves once immediately (boot locks to active
      // until the first model settles), then on every signal change.
      const unsubPower = subscribePowerProfile(profile => eng.setPowerProfile(profile))

      // Drag velocity listener for 3D physics tilt/swing during window dragging
      const unsubDragVelocity = $dragVelocity.listen(vel => {
        eng.character.setDragVelocity(vel.vx, vel.vy)
      })

      // Pixel-exact click-through refinement for 3D mode (static images use
      // their own hitmap — see sprite-stage).
      const detachSilhouette = attachSilhouetteHitProbe(eng)

      eng.start()

      detachWiring = () => {
        unsubState()
        unsubEmotion()
        unsubGenerated()
        detachLipSync()
        unsubPower()
        unsubDragVelocity()
        detachSilhouette()
        window.removeEventListener('resize', onResize)
        ro.disconnect()
        eng.canvas.removeEventListener('pointermove', onPointerMove)
        eng.canvas.removeEventListener('pointerleave', onPointerLeave)
      }

      return created
    })

    engineReadyRef.current = ready

    void hydrateGeneratedClips()
    void hydrateExpressions()

    void ready.catch(err => {
      // Whole fallback chain failed — no GPU context at all. Static-sprite
      // mode is the never-blank floor; settled so the scheduler can stand down.
      if (!cancelled) {
        log.error('companion-3d', 'engine init failed:', err)
        $glbLoadFailed.set(true)
        $modelLoadSettled.set(true)
      }
    })

    return () => {
      cancelled = true
      detachWiring?.()
      engine?.dispose()
    }
  }, [])

  // Load (or reload) the GLB whenever the model's asset URL changes. Awaits
  // engine boot — an early return on a null engine would silently skip the
  // first model forever.
  useEffect(() => {
    let cancelled = false
    const url = modelInfo.asset_url

    // A different model resets the render-style override to the model's own
    // seed style (legacy rows default to realistic/PBR).
    if (modelInfo.id !== null) {
      $renderStyle.set(modelInfo.style)
    }
    // Fetch signed bytes via IPC — main re-bases the host, so no CORS preflight.
    // Leverages disk cache & Range resumption when content_hash is present.
    // Null on fetch failure lets CharacterController fall through to procedural.
    void (async () => {
      const engine = await engineReadyRef.current

      if (!engine || cancelled) {
        return
      }

      let bytes: ArrayBuffer | null = null

      if (url) {
        try {
          // OPFS-cached first; falls back to the main-process IPC and
          // populates the cache on success. The first load still costs the
          // IPC round-trip; subsequent loads with the same contentHash are
          // instant.
          bytes = await fetchGlbWithCache(url, modelInfo.content_hash || undefined)

          if (cancelled) {
            return
          }
        } catch (err) {
          if (cancelled) {
            return
          }

          log.warn('companion-3d', 'GLB fetch failed, using procedural fallback:', err)
        }
      }

      try {
        const info = await engine.loadCharacter(
          bytes,
          modelInfo.rig_type || 'biped',
          modelInfo.content_hash || undefined
        )

        if (cancelled) {
          return
        }

        // Publish whether the engine fell through to the procedural egg —
        // static-mode waits on this, not on model.ready, so the swap to 3D
        // happens only once the GLB has actually parsed (no egg flash).
        $glbLoadFailed.set(info.procedural)
        $modelLoadSettled.set(true)

        // Warm up the live silhouette hitmap immediately with the newly loaded character.
        void engine.silhouetteHitmap().then(map => {
          if (map && !cancelled) {
            probeInteractiveRegions()
          }
        })
      } catch (err) {
        if (cancelled) {
          return
        }

        log.error('companion-3d', 'loadCharacter failed:', err)
        $glbLoadFailed.set(true)
        $modelLoadSettled.set(true)

        return
      }

      if (cancelled) {
        return
      }

      // Re-apply morph params + the currently equipped outfit so reloads
      // preserve the user's customisation.
      if (Object.keys(modelInfo.morph_params).length) {
        engine.character.setMorphs(modelInfo.morph_params)
      }

      refreshEquippedAndApply()
      // Reloads keep the active render style (StyleDirector re-applies it
      // to the fresh root during attach).
      engine.setRenderStyle($renderStyle.get())
    })()

    return () => {
      cancelled = true
    }
  }, [
    modelInfo.asset_url,
    modelInfo.content_hash,
    modelInfo.id,
    modelInfo.morph_params,
    modelInfo.rig_type,
    modelInfo.style
  ])

  // Hot-switch NPR ⇄ PBR. Gated on the first model settling so the egg
  // window never flips lighting presets pointlessly.
  useEffect(() => {
    let cancelled = false

    void (async () => {
      const engine = await engineReadyRef.current

      if (!engine || cancelled) {
        return
      }

      engine.setRenderStyle(renderStyle)
    })()

    return () => {
      cancelled = true
    }
  }, [renderStyle])

  // Apply the equipped set (or a live preview candidate) on every change. A
  // preview replaces only the equipped item in its own slot — other slots keep
  // rendering so a shirt preview doesn't visually strip the equipped shoes.
  // setOutfit is a no-op when the character is the procedural fallback.
  useEffect(() => {
    let cancelled = false
    const view = outfitView

    void (async () => {
      const engine = await engineReadyRef.current

      if (!engine || cancelled) {
        return
      }

      engine.character.setOutfit(view)
    })()

    return () => {
      cancelled = true
    }
  }, [outfitView])

  const genState = useStore($modelGenState)
  const genProgress = useStore($modelGenProgress)
  const genError = useStore($modelGenError)
  const retryable = useStore($modelRetryable)
  const retryModelId = useStore($modelRetryModelId)
  const staticMode = useStore($staticMode)
  const activeSprite = useStore($activeSprite)
  const isStaticCovered = Boolean(staticMode && activeSprite)

  // The failed panel carries the retry button — without a registered region
  // the click passes through the transparent sprite window to the desktop
  // (interactive-regions.ts owns capture toggling). Rect is null while the
  // panel is unmounted, so the region only exists in the failed state.
  const failedPanelRef = useRef<HTMLDivElement | null>(null)
  useInteractiveRegion('model-gen-failed', failedPanelRef)

  return (
    <div
      className="companion-3d-wrapper"
      data-static-covered={isStaticCovered ? 'true' : undefined}
      ref={containerRef}
      style={{ position: 'relative', width: '100%', height: '100%' }}
    >
      {genState === 'generating' && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 20,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
            padding: '1rem'
          }}
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              padding: '8px 16px',
              borderRadius: '16px',
              background: 'rgba(0, 0, 0, 0.7)',
              backdropFilter: 'blur(8px)',
              border: '1px solid rgba(255, 255, 255, 0.15)',
              boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)',
              maxWidth: '85%'
            }}
          >
            <div
              style={{
                fontSize: '0.7rem',
                color: 'rgba(255, 255, 255, 0.95)',
                marginBottom: '0.4rem',
                fontWeight: 500
              }}
            >
              ✨ 正在为你塑造形象…
            </div>
            <div
              style={{
                width: '100px',
                height: '3px',
                background: 'rgba(255, 255, 255, 0.2)',
                borderRadius: '2px',
                overflow: 'hidden'
              }}
            >
              <div
                style={{
                  width: `${genProgress?.progress ?? 0}%`,
                  height: '100%',
                  background: 'rgba(255, 255, 255, 0.9)',
                  borderRadius: '2px',
                  transition: 'width 0.5s ease'
                }}
              />
            </div>
            {genProgress?.stage && (
              <div style={{ fontSize: '0.6rem', color: 'rgba(255, 255, 255, 0.65)', marginTop: '0.3rem' }}>
                {stageLabel(genProgress.stage)}
              </div>
            )}
          </div>
        </div>
      )}
      {!isStaticCovered && genState === 'failed' && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 20,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
            padding: '1rem'
          }}
        >
          <div
            ref={failedPanelRef}
            style={{
              padding: '6px 14px',
              borderRadius: '14px',
              background: 'rgba(0, 0, 0, 0.7)',
              backdropFilter: 'blur(8px)',
              border: '1px solid rgba(255, 100, 100, 0.25)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.3)',
              maxWidth: '85%'
            }}
          >
            <div style={{ fontSize: '0.65rem', color: 'rgba(255, 180, 180, 0.95)', textAlign: 'center' }}>
              {genError ?? '3D 模型生成失败'}
            </div>
            {retryable && (
              <button
                onClick={() => retryModelId !== null && void retryModelDownload(retryModelId)}
                style={{
                  marginTop: '0.45rem',
                  pointerEvents: 'auto',
                  padding: '4px 14px',
                  fontSize: '0.65rem',
                  color: 'rgba(255, 255, 255, 0.95)',
                  background: 'rgba(90, 140, 255, 0.35)',
                  border: '1px solid rgba(140, 180, 255, 0.5)',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  width: '100%'
                }}
                type="button"
              >
                重试下载
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

const STAGE_LABELS: Record<string, string> = {
  uploading: '上传种子图…',
  generating: '生成 3D 几何…',
  checking_rig: '检测骨骼结构…',
  rigging: '绑定骨骼…',
  downloading: '下载模型…',
  injecting_morphs: '注入表情…',
  finalizing: '保存中…',
  done: '完成！'
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}
