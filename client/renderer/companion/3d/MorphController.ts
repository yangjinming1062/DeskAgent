import * as THREE from 'three'

/** Facial morph control for the two surviving face signals: the automatic
 * blink and TTS lip-sync. Emotion expressions moved off the 3D face (faces
 * are too small to read) — the chat dock swaps a generated avatar image
 * instead; the 3D body still plays emotion clips. Canonical semantic name →
 * possible morph target names across model formats; the first alias that
 * exists in the loaded model's dictionary wins. */
const ALIASES: Record<string, string[][]> = {
  // 优先统一眨眼；单眼眨眼组在没有统一 morph 的模型上会让双眼一起闭上
  // （孤立 wink 没有消费者，因为情绪 morph 把表情搬走了）。
  blink: [
    ['blink', 'eyeBlink', 'eyesClosed', 'Blink', 'eye_blink'],
    ['eyeBlinkLeft', 'Blink_Left', 'blink_l', 'eye_blink_left'],
    ['eyeBlinkRight', 'Blink_Right', 'blink_r', 'eye_blink_right']
  ],
  jawOpen: [['jawOpen', 'mouthOpen', 'Open', 'aa', 'mouth_open']]
}

export class MorphController {
  private meshes: THREE.Mesh[] = []
  private resolved: Record<string, [number, number][]> = {}
  /** Cached at discovery time to avoid per-frame allocations. */
  private resolvedEntries: [string, [number, number][]][] = []
  private blinkHits: [number, number][] = []
  private blinkTimer = 0
  private blinkInterval = 3 + Math.random() * 3
  private blinkPhase: 'idle' | 'closing' | 'opening' = 'idle'
  private blinkElapsed = 0
  private lipSyncAmplitude = 0
  // 平滑的张嘴覆盖——非对称的 attack/release 能去掉音频开始时的嘴部"啪"声与结束时突然闭合的突兀感（airi 模式）。
  private currentJawValue = 0

  setLipSyncAmplitude(amp: number): void {
    this.lipSyncAmplitude = amp
  }

  discover(root: THREE.Object3D): void {
    this.meshes = []
    this.resolved = {}

    root.traverse(child => {
      if (child instanceof THREE.Mesh && child.morphTargetDictionary && child.morphTargetInfluences) {
        this.meshes.push(child)
      }
    })

    if (this.meshes.length === 0) {
      return
    }

    for (const [semantic, aliasGroups] of Object.entries(ALIASES)) {
      const hits: [number, number][] = []

      for (let mi = 0; mi < this.meshes.length; mi++) {
        const dict = this.meshes[mi].morphTargetDictionary!

        let unifiedMatched = false

        if (aliasGroups.length > 1) {
          for (const alias of aliasGroups[0]) {
            if (alias in dict) {
              hits.push([mi, dict[alias]])
              unifiedMatched = true

              break
            }
          }
        }

        if (!unifiedMatched) {
          const startIdx = aliasGroups.length > 1 ? 1 : 0

          for (let gi = startIdx; gi < aliasGroups.length; gi++) {
            for (const alias of aliasGroups[gi]) {
              if (alias in dict) {
                hits.push([mi, dict[alias]])

                break
              }
            }
          }
        }
      }

      if (hits.length > 0) {
        this.resolved[semantic] = hits
      }
    }

    this.resolvedEntries = Object.entries(this.resolved)
    this.blinkHits = this.resolved['blink'] ?? []
  }

  hasTargets(): boolean {
    return this.meshes.length > 0
  }

  targetNames(): string[] {
    const names = new Set<string>()

    for (const mesh of this.meshes) {
      if (mesh.morphTargetDictionary) {
        for (const name of Object.keys(mesh.morphTargetDictionary)) {
          names.add(name)
        }
      }
    }

    return [...names]
  }

  update(delta: number): void {
    if (this.meshes.length === 0) {
      return
    }

    // 把漂移的影响衰减回中性脸——模型出厂默认值可能非零，
    // 而这两组 morph 在眨眼/唇形同步之外没有其他写入。
    const speed = Math.min(1, 8 * delta)

    for (const [, hits] of this.resolvedEntries) {
      for (const [mi, ti] of hits) {
        const infls = this.meshes[mi].morphTargetInfluences!

        if (infls[ti] !== undefined) {
          const cur = infls[ti]

          if (Math.abs(cur) > 0.001) {
            infls[ti] = THREE.MathUtils.lerp(cur, 0, speed)
          } else if (cur !== 0) {
            infls[ti] = 0
          }
        }
      }
    }

    this.updateBlink(delta)
    this.updateLipSync(delta)
  }

  /**
   * Asymmetric smoothing on the audio-driven jaw override — fast attack, slow release (airi pattern).
   * The override is `max`'d with the decayed jaw so audio can drive the mouth open; the decay loop
   * above then re-zeros it once speech ends.
   */
  private updateLipSync(delta: number): void {
    const target = this.lipSyncAmplitude * 0.6

    // 上升时快速 attack（嘴迅速张开），下降时缓慢 release（嘴柔和闭合）。
    // 在 delta 极大时（例如标签页暂停后）clamp 保证 lerp 系数帧率正确：
    // 没有 clamp，lerp 会越过 target 并振荡。
    const speed = target > this.currentJawValue ? 50 : 8
    this.currentJawValue = THREE.MathUtils.lerp(this.currentJawValue, target, Math.min(1, speed * delta))

    const jawHits = this.resolved['jawOpen']

    if (!jawHits) {
      return
    }

    for (const [mi, ti] of jawHits) {
      const infls = this.meshes[mi].morphTargetInfluences!

      if (infls[ti] !== undefined) {
        infls[ti] = Math.max(infls[ti], this.currentJawValue)
      }
    }
  }

  private updateBlink(delta: number): void {
    if (this.blinkHits.length === 0) {
      return
    }

    if (this.blinkPhase === 'idle') {
      this.blinkTimer += delta

      if (this.blinkTimer >= this.blinkInterval) {
        this.blinkPhase = 'closing'
        this.blinkElapsed = 0
        this.blinkTimer = 0
        this.blinkInterval = 3 + Math.random() * 3
      }

      return
    }

    this.blinkElapsed += delta
    const half = 0.12
    let amount: number

    if (this.blinkElapsed < half) {
      amount = this.blinkElapsed / half
    } else if (this.blinkElapsed < half * 2) {
      amount = 1 - (this.blinkElapsed - half) / half
    } else {
      amount = 0
      this.blinkPhase = 'idle'
    }

    for (const [mi, ti] of this.blinkHits) {
      const infls = this.meshes[mi].morphTargetInfluences!

      if (infls[ti] !== undefined) {
        infls[ti] = amount
      }
    }
  }
}
