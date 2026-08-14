import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { registerAmplitudeSink } from '@/companion/audio-track'
import { $chatOpen } from '@/companion/chat-store'
import {
  $clipOverride,
  $spriteEmotion,
  $spriteState,
  type SpriteEmotion,
  type SpriteStateName
} from '@/companion/companion-store'
import { $personalityTags } from '@/companion/persona-store'
import { log } from '@/shared/lib/log'

import { Engine } from './Engine'
import {
  $expressions,
  $generatedClips,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  $modelInfo,
  $outfitView,
  hydrateExpressions,
  hydrateGeneratedClips,
  refreshEquippedAndApply
} from './model-store'

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
  const outfitView = useStore($outfitView)
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
    const initialTags = $personalityTags.get()
    engine.character.applyState(initial.state, initial.emotion, {
      companionTags: initialTags
    })

    const initialGenerated = $generatedClips.get()

    if (initialGenerated.length > 0) {
      engine.character.appendClipDefs(initialGenerated)
    }

    const unsubState = $spriteState.listen(state => {
      const tags = $personalityTags.get()
      const override = state === 'interacting' ? $clipOverride.get() : undefined
      engine.character.applyState(state, $spriteEmotion.get(), {
        companionTags: tags,
        clipOverride: override
      })

      if (state !== 'interacting') {
        $clipOverride.set(null)
      }
    })

    const unsubEmotion = $spriteEmotion.listen(emotion => {
      engine.character.applyState($spriteState.get(), emotion, {
        companionTags: $personalityTags.get(),
        customExpressions: $expressions.get()
      })
    })

    const unsubGenerated = $generatedClips.listen(clips => {
      if (clips.length > 0) {
        engine.character.appendClipDefs(clips)
      }
    })

    const unsubExpressions = $expressions.listen(exprs => {
      if (exprs.length > 0) {
        engine.character.setCustomExpressions(exprs)
      }
    })

    void hydrateGeneratedClips()
    void hydrateExpressions()

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
      unsubGenerated()
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
    const url = modelInfo.asset_url

    // Fetch signed bytes via IPC — main re-bases the host, so no CORS preflight.
    // Leverages disk cache & Range resumption when content_hash is present.
    // Null on fetch failure lets CharacterController fall through to procedural.
    void (async () => {
      let bytes: ArrayBuffer | null = null

      if (url) {
        try {
          const u8 = await window.deskagent.apiAssetBuffer({
            url,
            contentHash: modelInfo.content_hash || undefined
          })

          if (cancelled) {
            return
          }

          bytes = u8.slice().buffer
        } catch (err) {
          if (cancelled) {
            return
          }

          log.warn('companion-3d', 'GLB fetch failed, using procedural fallback:', err)
        }
      }

      try {
        await engine.loadCharacter(bytes, modelInfo.rig_type || 'biped')
      } catch (err) {
        if (cancelled) {
          return
        }

        log.error('companion-3d', 'loadCharacter failed:', err)

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
    })()

    return () => {
      cancelled = true
    }
  }, [modelInfo.asset_url, modelInfo.content_hash, modelInfo.morph_params, modelInfo.rig_type])

  // Apply the equipped set (or a live preview candidate) on every change. A
  // preview replaces only the equipped item in its own slot — other slots keep
  // rendering so a shirt preview doesn't visually strip the equipped shoes.
  // setOutfit is a no-op when the character is the procedural fallback.
  useEffect(() => {
    const engine = engineRef.current

    if (!engine) {
      return
    }

    engine.character.setOutfit(outfitView)
  }, [outfitView])

  const genState = useStore($modelGenState)
  const genProgress = useStore($modelGenProgress)
  const genError = useStore($modelGenError)

  return (
    <div className="companion-3d-wrapper" style={{ position: 'relative', width: '100%', height: '100%' }}>
      <canvas className="companion-3d-canvas" ref={canvasRef} />
      {genState === 'generating' && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(0,0,0,0.4)',
            backdropFilter: 'blur(2px)',
            borderRadius: '50%',
            pointerEvents: 'none'
          }}
        >
          <div style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.8)', marginBottom: '0.4rem' }}>
            ✨ 正在为你塑造形象…
          </div>
          <div
            style={{
              width: '60%',
              height: '3px',
              background: 'rgba(255,255,255,0.15)',
              borderRadius: '2px',
              overflow: 'hidden'
            }}
          >
            <div
              style={{
                width: `${genProgress?.progress ?? 0}%`,
                height: '100%',
                background: 'rgba(255,255,255,0.7)',
                borderRadius: '2px',
                transition: 'width 0.5s ease'
              }}
            />
          </div>
          {genProgress?.stage && (
            <div style={{ fontSize: '0.6rem', color: 'rgba(255,255,255,0.5)', marginTop: '0.3rem' }}>
              {stageLabel(genProgress.stage)}
            </div>
          )}
        </div>
      )}
      {genState === 'failed' && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(0,0,0,0.4)',
            backdropFilter: 'blur(2px)',
            borderRadius: '50%',
            pointerEvents: 'none'
          }}
        >
          <div style={{ fontSize: '0.65rem', color: 'rgba(255,180,180,0.9)', textAlign: 'center', maxWidth: '80%' }}>
            {genError ?? '生成失败'}
          </div>
        </div>
      )}
    </div>
  )
}

const STAGE_LABELS: Record<string, string> = {
  stylizing: '风格化预处理…',
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
