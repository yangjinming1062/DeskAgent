import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

import { registerAmplitudeSink } from '@/companion/audio-track'
import { $personalityTags } from '@/companion/persona-store'
import { $spatialLocomotion, type Locomotion } from '@/companion/spatial'
import { log } from '@/shared/lib/log'

import { $contextMenuOpen } from '../sprite/context-menu-store'

import { type Mesh2DAnimations, Mesh2DDriver } from './mesh2d-drivers'
import { Mesh2DHitmap } from './mesh2d-hitmap'
import { loadMesh2DManifest } from './mesh2d-loader'
import { buildMesh2DScene, type Manifest, type Mesh2DScene, tickMesh2D } from './mesh2d-runtime'
import { $mesh2dHitmap, $mesh2dInfo, $mesh2dReady, setMesh2DHitmap } from './mesh2d-store'

const REDUCED_MOTION_QUERY =
  typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null

const prefersReducedMotion = (): boolean => REDUCED_MOTION_QUERY?.matches === true

// persona 标签 → idle variant 偏好权重（DESIGN.md §2.3）。
// 让粘人 / 活泼 / 害羞等性格在空闲时的微动有差异化表现，而不是全员等权重。
// 标签可同时命中多组，权重累加：所有 variant 至少保留 0.4 兜底，避免极端性格导致某姿态永不出现。
const IDLE_VARIANT_BOOST: ReadonlyArray<readonly [readonly string[], string]> = [
  [['活泼', '好动', '元气', '调皮', '俏皮', '热血', '搞怪', '狂野'], 'idle_sway_more'],
  [['好奇', '敏锐', '灵动', '聪明', '警觉锐利', '知性', '理性'], 'idle_glance'],
  [['害羞', '社恐', '内敛', '安静', '胆小', '温婉', '文静'], 'idle_squint'],
  [['沉稳', '冷静', '懒散', '慵懒', '高冷', '清冷', '孤傲', '从容不迫'], 'idle_breath']
]

function buildIdleWeightsFromPersona(tags: readonly string[], variants: readonly string[]): Record<string, number> {
  const weights: Record<string, number> = {}

  for (const v of variants) {
    weights[v] = 0.4
  }

  for (const tag of tags) {
    for (const [keywords, variant] of IDLE_VARIANT_BOOST) {
      if (keywords.includes(tag) && variant in weights) {
        weights[variant] = (weights[variant] ?? 0.4) + 1.6
      }
    }
  }

  return weights
}

export function Mesh2DCanvas(): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<Mesh2DScene | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const cameraRef = useRef<THREE.OrthographicCamera | null>(null)
  const animationRef = useRef<number | null>(null)
  const lastTimeRef = useRef<number>(0)
  const audioAmpRef = useRef<number>(0)
  const lookRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const startedAtRef = useRef<number>(performance.now())
  const driverRef = useRef<Mesh2DDriver | null>(null)
  // locomotion 通过 ref 透传给 tick 闭包（避免把 atom 加到 useEffect 依赖）
  const locomotionRef = useRef<Locomotion>('still')
  // hover impulse 节流：避免 pointermove 高频触发
  const lastHairImpulseAtRef = useRef<number>(0)
  const lastSkirtImpulseAtRef = useRef<number>(0)
  const [error, setError] = useState<string | null>(null)

  const mesh2d = useStore($mesh2dInfo)
  const ready = useStore($mesh2dReady)
  const locomotion = useStore($spatialLocomotion)

  // 把 locomotion 同步到 ref（atom 变化由 nanostores 推，ref 在 tick 里读最新值）
  useEffect(() => {
    locomotionRef.current = locomotion
  }, [locomotion])

  // 当 manifestUrl 变化时重新加载场景。
  useEffect(() => {
    if (!mesh2d.manifestUrl) {
      return
    }

    const container = containerRef.current

    if (!container) {
      return
    }

    let cancelled = false
    let scene: Mesh2DScene | null = null
    let renderer: THREE.WebGLRenderer | null = null
    let camera: THREE.OrthographicCamera | null = null
    let driver: Mesh2DDriver | null = null

    void (async () => {
      try {
        const manifest = await loadMesh2DManifest(mesh2d.manifestUrl!, mesh2d.contentHash ?? undefined)

        if (cancelled) {
          return
        }

        scene = await buildMesh2DScene(manifest, mesh2d.layerUrls)
        sceneRef.current?.dispose()
        sceneRef.current = scene

        // 构建 hitmap 并暴露给全局（SpriteStage 在 tap / pointermove 时调用）
        const hitmap = new Mesh2DHitmap(manifest)
        setMesh2DHitmap({
          hit: (nx, ny) => {
            const r = hitmap.hitRegion(nx, ny)

            return r ? { region: r.region } : null
          }
        })

        const w = container.clientWidth || scene.width
        const h = container.clientHeight || scene.height

        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
        renderer.setPixelRatio(window.devicePixelRatio || 1)
        renderer.setSize(w, h)
        renderer.setClearColor(0x000000, 0)
        container.appendChild(renderer.domElement)
        rendererRef.current = renderer

        const halfW = w / 2
        const halfH = h / 2
        camera = new THREE.OrthographicCamera(-halfW, halfW, halfH, -halfH, -1000, 1000)
        camera.position.z = 100
        cameraRef.current = camera

        scene.group.position.set(w / 2 - scene.width / 2, h / 2 - scene.height / 2, 0)

        lastTimeRef.current = performance.now()
        startedAtRef.current = lastTimeRef.current

        // 实例化 driver（订阅 $spriteState/$spriteEmotion/$spriteAction）
        const animations = manifest.animations as Manifest['animations'] & Mesh2DAnimations
        const idleVariants = animations.idle_variants ?? []
        driver = new Mesh2DDriver(
          scene,
          {
            breath: animations.breath,
            blink: animations.blink,
            idle_sway: animations.idle_sway,
            jiggle: animations.jiggle,
            red_lines: animations.red_lines ?? {},
            actions: animations.actions ?? {},
            idle_variants: idleVariants,
            locomotion: (animations.locomotion ?? {}) as Mesh2DAnimations['locomotion']
          },
          {
            idleWeights: buildIdleWeightsFromPersona($personalityTags.get(), idleVariants)
          }
        )
        driverRef.current = driver

        const tick = (now: number) => {
          if (cancelled || !renderer || !camera || !scene || !driver) {
            return
          }

          const dt = (now - lastTimeRef.current) / 1000
          lastTimeRef.current = now

          // 1. driver 写入 base pose（active action + locomotion + idle variant）
          driver.tick(now, dt, locomotionRef.current)
          // 2. 把 base pose 快照到 bone.userData，供 micro-motion 叠加
          driver.cacheBasePose()
          // 3. micro-motion / 视线跟随 / 嘴型 / jiggle 在 base 之上叠加
          tickMesh2D(scene, {
            dt,
            elapsed: now - startedAtRef.current,
            audioAmp: audioAmpRef.current,
            lookX: lookRef.current.x,
            lookY: lookRef.current.y,
            breathActive: true,
            blinkActive: true,
            reducedMotion: prefersReducedMotion(),
            eyeSquint: driver.getActiveActionName() === 'petting'
          })

          renderer.render(scene.group, camera)
          animationRef.current = requestAnimationFrame(tick)
        }

        animationRef.current = requestAnimationFrame(tick)
        setError(null)
      } catch (err) {
        log.warn('mesh2d-canvas', 'failed to build scene', err)
        setError(err instanceof Error ? err.message : String(err))
      }
    })()

    return () => {
      cancelled = true

      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
        animationRef.current = null
      }

      driver?.dispose()
      driverRef.current = null

      if ($mesh2dHitmap.get()) {
        setMesh2DHitmap(null)
      }

      scene?.dispose()
      sceneRef.current = null

      if (renderer) {
        renderer.dispose()
        renderer.domElement.remove()
        rendererRef.current = null
      }

      cameraRef.current = null
    }
  }, [mesh2d.manifestUrl, mesh2d.contentHash, mesh2d.layerUrls, ready])

  // TTS 振幅转发：直接驱动 mouth 骨骼。
  useEffect(
    () =>
      registerAmplitudeSink(amp => {
        audioAmpRef.current = amp
      }),
    []
  )

  // persona 变化时把新的 idle 权重推到 driver（不必重建——只是改采样表）。
  // 初始 mount 时 driver 还未就绪，所以 buildIdleWeightsFromPersona 在 init effect 里同时跑一次。
  const personalityTags = useStore($personalityTags)

  useEffect(() => {
    const d = driverRef.current

    if (!d) {
      return
    }

    d.setIdleWeights(buildIdleWeightsFromPersona(personalityTags, d.getIdleVariants()))
  }, [personalityTags])

  // 视线跟随 + hover impulse：pointermove 时更新 lookTarget，并对 hair / skirt 区域触发 jiggle。
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const container = containerRef.current

      if (!container || $contextMenuOpen.get()) {
        lookRef.current = { x: 0, y: 0 }

        return
      }

      const rect = container.getBoundingClientRect()
      const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1
      const ny = ((e.clientY - rect.top) / rect.height) * 2 - 1
      lookRef.current = { x: nx, y: ny }

      const hit = $mesh2dHitmap.get()
      const d = driverRef.current

      if (!hit || !d) {
        return
      }

      const localNx = (e.clientX - rect.left) / rect.width
      const localNy = (e.clientY - rect.top) / rect.height
      const result = hit.hit(localNx, localNy)

      if (!result) {
        return
      }

      switch (result.region) {
        case 'back_hair':

        case 'front_hair':
          // 200ms 节流避免 hover 抖动
          if (performance.now() - lastHairImpulseAtRef.current > 200) {
            d.triggerImpulse(result.region, 1.5)
            lastHairImpulseAtRef.current = performance.now()
          }

          break

        case 'skirt':
          if (performance.now() - lastSkirtImpulseAtRef.current > 200) {
            d.triggerImpulse('skirt', 2.0)
            lastSkirtImpulseAtRef.current = performance.now()
          }

          break

        default:
          break
      }
    }

    const onLeave = () => {
      lookRef.current = { x: 0, y: 0 }
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerleave', onLeave)

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerleave', onLeave)
    }
  }, [])

  return (
    <div className="mesh2d-canvas" ref={containerRef} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      {error && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none'
          }}
        >
          <ProceduralEgg />
        </div>
      )}
    </div>
  )
}

// 2D 模式资产失败时的程序化蛋兜底——DESIGN §1.2「永不空白」级联。
// 与桌面蛋视觉一致：呼吸 + 眼睛注视 + 裂纹闪光；只是没有 prompt（已是常驻伴侣上下文）。
function ProceduralEgg(): React.JSX.Element {
  const eyesRef = useRef<SVGGElement>(null)

  useEffect(() => {
    let pointerActive = false
    let pointerLook = { x: 0, y: 0 }

    const updateEyes = (x: number, y: number) => {
      if (eyesRef.current) {
        eyesRef.current.style.transform = `translate3d(${x * 3.2}px, ${y * 3.2}px, 0)`
      }
    }

    const onMove = (e: PointerEvent) => {
      const nx = ((e.clientX / window.innerWidth) * 2 - 1) * 0.6
      const ny = ((e.clientY / window.innerHeight) * 2 - 1) * 0.4
      pointerActive = true
      pointerLook = { x: nx, y: ny }
      updateEyes(nx, ny)
    }

    const onLeave = () => {
      pointerActive = false
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerleave', onLeave)

    let raf = 0
    const startedAt = performance.now()

    const tick = (now: number) => {
      if (!pointerActive) {
        const t = (now - startedAt) / 3200
        updateEyes(Math.sin(t * Math.PI * 2) * 0.18, Math.cos(t * Math.PI) * 0.12)
      } else {
        updateEyes(pointerLook.x, pointerLook.y)
      }

      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerleave', onLeave)
      cancelAnimationFrame(raf)
    }
  }, [])

  return (
    <svg aria-label="Procedural Egg" className="overflow-visible" height="240" viewBox="0 0 320 320" width="240">
      <defs>
        <radialGradient cx="50%" cy="50%" id="mesh2d-egg-glow" r="50%">
          <stop offset="0%" stopColor="#ffd166" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#ffd166" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse className="animate-egg-pulse" cx="160" cy="170" fill="url(#mesh2d-egg-glow)" rx="125" ry="135" />
      <g className="animate-egg-breath">
        <path
          d="M 160 50 C 95 50 75 135 75 185 C 75 245 112 290 160 290 C 208 290 245 245 245 185 C 245 135 225 50 160 50 Z"
          fill="var(--color-egg-shell, #fff4d6)"
          stroke="rgba(0,0,0,0.12)"
          strokeWidth="2"
        />
        <g ref={eyesRef} style={{ willChange: 'transform' }}>
          <ellipse cx={156} cy={155} fill="#1a1a2e" rx="6" ry="9" />
          <ellipse cx={184} cy={155} fill="#1a1a2e" rx="6" ry="9" />
        </g>
        <ellipse cx="170" cy="195" fill="#c89060" opacity="0.85" rx="6" ry="3.5" />
      </g>
    </svg>
  )
}
