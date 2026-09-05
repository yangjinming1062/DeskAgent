import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useEffect, useMemo, useRef } from 'react'

import { $puppetReady, $renderMode, hydrateMesh2D, hydratePuppet } from '@/2d'
import { $glbLoadFailed, $modelInfo, hydrateExpressions, hydrateModel } from '@/3d'
import {
  $companionLifecycle,
  emitVfx,
  ensureCompanionHydrated,
  FootGlow,
  handlePetInteraction,
  hydratePersona,
  hydratePortrait,
  Mesh2DVfxOverlay,
  reportUserActivity,
  resolveCompanionRenderLayer
} from '@/companion'
import { EggStage } from '@/onboarding'
import { $auth } from '@/shared/store/auth'

import styles from './workbench.module.css'

const Companion3D = lazy(() => import('@/3d').then(m => ({ default: m.Companion3D })))
const PuppetStage = lazy(() => import('@/2d').then(m => ({ default: m.PuppetStage })))

export function WorkbenchCompanion(): React.JSX.Element {
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)
  const renderMode = useStore($renderMode)
  const puppetReady = useStore($puppetReady)
  const modelInfo = useStore($modelInfo)
  const glbLoadFailed = useStore($glbLoadFailed)
  const pointerStartRef = useRef<{ time: number; x: number; y: number } | null>(null)
  const hasHydratedRef = useRef(false)

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready') {
      hasHydratedRef.current = false

      return
    }

    if (!hasHydratedRef.current) {
      hasHydratedRef.current = true

      void ensureCompanionHydrated({
        hydrateExpressions,
        hydrateMesh2D,
        hydrateModel,
        hydratePersona,
        hydratePortrait,
        hydratePuppet
      })
    }

    return () => {
      hasHydratedRef.current = false
    }
  }, [auth.kind, lifecycle])

  const renderLayer = useMemo<'companion3d' | 'puppet'>(() => {
    return resolveCompanionRenderLayer({
      glbLoadFailed,
      modelStatus: modelInfo.status,
      puppetReady,
      renderMode
    })
  }, [renderMode, puppetReady, glbLoadFailed, modelInfo.status])

  const handleTap = (): void => {
    reportUserActivity()

    if (auth.kind === 'authenticated') {
      handlePetInteraction()
    } else {
      emitVfx('heart', { count: 3, nx: 0.5, ny: 0.25 })
    }
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0) {
      return
    }

    pointerStartRef.current = { time: performance.now(), x: e.clientX, y: e.clientY }
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>): void => {
    const start = pointerStartRef.current
    pointerStartRef.current = null

    if (!start) {
      return
    }

    const dist = Math.hypot(e.clientX - start.x, e.clientY - start.y)
    const elapsed = performance.now() - start.time

    // 微小位移且短按视为点击戳击/摸头反馈；长按或位移则由系统原生拖拽接管
    if (dist <= 6 && elapsed < 400) {
      handleTap()
    }
  }

  return (
    <div
      className={styles.companionWrapper}
      onContextMenu={e => {
        e.preventDefault()
      }}
      onPointerCancel={() => {
        pointerStartRef.current = null
      }}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      title="SpiritAgent 伴工精灵（按住可拖动整个工作台，轻点互动）"
    >
      <div className={styles.companionInner}>
        <FootGlow />
        <Suspense fallback={null}>
          {auth.kind !== 'authenticated' ? (
            <EggStage onTap={handleTap} />
          ) : renderLayer === 'puppet' ? (
            <PuppetStage />
          ) : (
            <Companion3D />
          )}
        </Suspense>
        <Mesh2DVfxOverlay />
      </div>
    </div>
  )
}
