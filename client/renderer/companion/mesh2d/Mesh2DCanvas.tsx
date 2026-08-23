import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

import { registerAmplitudeSink } from '@/companion/audio-track'
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
        const manifest = await loadMesh2DManifest(mesh2d.manifestUrl!)

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
        driver = new Mesh2DDriver(scene, {
          breath: animations.breath,
          blink: animations.blink,
          idle_sway: animations.idle_sway,
          jiggle: animations.jiggle,
          red_lines: animations.red_lines ?? {},
          actions: animations.actions ?? {},
          idle_variants: animations.idle_variants ?? [],
          locomotion: (animations.locomotion ?? {}) as Mesh2DAnimations['locomotion']
        })
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
            reducedMotion: prefersReducedMotion()
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
            color: '#fff',
            fontSize: 12,
            opacity: 0.6,
            pointerEvents: 'none'
          }}
        >
          2D 渲染加载失败
        </div>
      )}
    </div>
  )
}
