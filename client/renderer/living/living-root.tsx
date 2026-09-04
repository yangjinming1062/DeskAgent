// 生活空间根组件：房间背景 + 顶栏 + 左栏 + 右栏（视图路由）。
//
// 不在右栏挂 PuppetStage / Companion3D；立绘由房间背景图承担。
// 关掉时主进程互斥会把焦点还给工作台或精灵。

import { useEffect } from 'react'
import type React from 'react'

import { ArrowRight, Home } from '@/shared/lib/icons'
import { WindowControls } from '@/shared/panel'
import { hydrateRoomBackdrop } from '@/shared/store/room-backdrop-store'
import { requestOpenSurface } from '@/shared/store/surfaces'

import { LivingRail } from './living-rail'
import { LivingStage } from './living-stage'
import styles from './living.module.css'
import { RoomBackdrop } from './room-backdrop'

export function LivingRoot(): React.JSX.Element {
  useEffect(() => {
    void hydrateRoomBackdrop()
  }, [])

  return (
    <div className={styles.shell} data-surface="living">
      <RoomBackdrop />

      <header
        className={styles.titlebar}
        onDoubleClick={() => {
          void window.spiritagent?.surface?.maximize?.()
        }}
      >
        <div className={styles.titleArea}>
          <Home className={styles.titleIcon} size={18} />
          <h1 className={styles.title}>生活空间</h1>
          <div className={styles.statusBadge}>
            <span className={styles.statusDot} />
            <span>陪伴中</span>
          </div>
        </div>
        <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          <button
            className={styles.workbenchButton}
            onClick={() => {
              void requestOpenSurface('workbench')
            }}
            type="button"
          >
            <span>前往工作台</span>
            <ArrowRight size={13} />
          </button>
          <WindowControls />
        </div>
      </header>

      <div className={styles.body}>
        <LivingRail onGoToWorkbench={() => void requestOpenSurface('workbench')} />
        <main className={styles.stage}>
          <LivingStage />
        </main>
      </div>
    </div>
  )
}
