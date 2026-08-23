/** Mesh2D 多手势识别器。
 *
 * 识别以下手势：
 * 1. head_pat（摸头）：光标在 head/face/front_hair 区域内横向往复滑动（500ms 内 ≥2 次转向）；
 * 2. rapid_shake（狂甩）：拖拽或光标高速剧烈往复摆动（触发眩晕 dizzy）；
 * 3. poke_streak（连戳激怒）：由 interaction.ts 结合连戳频次调度。
 */

export interface GestureCallbacks {
  onPetStart?: (nx: number, ny: number) => void
  onPetTick?: (nx: number, ny: number) => void
  onPetEnd?: () => void
  onShakeDizzy?: () => void
}

interface PointerSample {
  x: number
  y: number
  time: number
}

export class Mesh2DGestureTracker {
  private samples: PointerSample[] = []
  private isPetting = false
  private lastPetTickAt = 0
  // pet 与 shake 是两个独立手势通道，各自维护方向与反转状态，避免互相污染：
  // pet 用 position 阈值（dx > 0.015），shake 用 velocity 阈值（vx > 0.0018），
  // 二者方向翻转检测条件不同，共享 lastDirX 会导致 shake 在 pet 结束后立刻把
  // 残留方向当作"反转"误触发。
  private lastPetDirX = 0
  private reversalCount = 0
  private lastReversalAt = 0
  private lastShakeDirX = 0
  private shakeReversals = 0
  private lastShakeReversalAt = 0
  private callbacks: GestureCallbacks

  constructor(callbacks: GestureCallbacks) {
    this.callbacks = callbacks
  }

  public feedPointerMove(nx: number, ny: number, isDown: boolean, region?: string | null): void {
    const now = performance.now()
    this.samples.push({ x: nx, y: ny, time: now })

    // 保留最近 800ms 的采样
    while (this.samples.length > 0 && now - this.samples[0]!.time > 800) {
      this.samples.shift()
    }

    if (this.samples.length < 3) {
      return
    }

    const prev = this.samples[this.samples.length - 2]!
    const dx = nx - prev.x
    const dt = Math.max(1, now - prev.time)
    const vx = dx / dt

    // 1. 摸头手势判定（head / face / front_hair 区域）
    const isHeadRegion = region === 'head' || region === 'face' || region === 'front_hair'

    if (isHeadRegion && Math.abs(dx) > 0.015) {
      const currentDirX = dx > 0 ? 1 : -1

      if (this.lastPetDirX !== 0 && currentDirX !== this.lastPetDirX) {
        if (now - this.lastReversalAt < 600) {
          this.reversalCount++
        } else {
          this.reversalCount = 1
        }

        this.lastReversalAt = now
      }

      this.lastPetDirX = currentDirX

      if (this.reversalCount >= 2) {
        if (!this.isPetting) {
          this.isPetting = true
          this.callbacks.onPetStart?.(nx, ny)
        }

        if (now - this.lastPetTickAt > 300) {
          this.lastPetTickAt = now
          this.callbacks.onPetTick?.(nx, ny)
        }
      }
    } else if (!isHeadRegion && this.isPetting) {
      this.endPetting()
    }

    // 2. 高速剧烈狂甩判定（仅在按住拖拽时）
    if (isDown && Math.abs(vx) > 0.0018) {
      const currentDirX = dx > 0 ? 1 : -1

      if (this.lastShakeDirX !== 0 && currentDirX !== this.lastShakeDirX) {
        if (now - this.lastShakeReversalAt < 350) {
          this.shakeReversals++
        } else {
          this.shakeReversals = 1
        }

        this.lastShakeReversalAt = now
        this.lastShakeDirX = currentDirX

        if (this.shakeReversals >= 4) {
          this.shakeReversals = 0
          this.callbacks.onShakeDizzy?.()
        }
      } else {
        this.lastShakeDirX = currentDirX
      }
    }
  }

  public feedPointerUp(): void {
    if (this.isPetting) {
      this.endPetting()
    }

    this.reversalCount = 0
    this.shakeReversals = 0
    this.samples = []
    this.lastPetDirX = 0
    this.lastShakeDirX = 0
  }

  public tick(now: number): void {
    if (this.isPetting && now - this.lastPetTickAt > 500) {
      this.endPetting()
    }
  }

  private endPetting(): void {
    this.isPetting = false
    this.reversalCount = 0
    this.lastPetDirX = 0
    this.callbacks.onPetEnd?.()
  }
}
