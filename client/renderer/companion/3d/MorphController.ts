import * as THREE from 'three'

/** Facial morph control for the two surviving face signals: the automatic
 * blink and TTS lip-sync. Emotion expressions moved off the 3D face (faces
 * are too small to read) — the chat dock swaps a generated avatar image
 * instead; the 3D body still plays emotion clips. Canonical semantic name →
 * possible morph target names across model formats; the first alias that
 * exists in the loaded model's dictionary wins. */
const ALIASES: Record<string, string[][]> = {
  // Unified blink first; per-eye groups close both eyes on models without a
  // unified morph (a lone wink has no consumer since emotion morphs left).
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
  // Smoothed jaw-open override — asymmetric attack/release kills the mouth "pop" on audio start and the abrupt close on audio end (airi pattern).
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

    // Decay any stray influences toward the neutral face — models may ship
    // with non-zero defaults and nothing else writes these two morph groups
    // outside blink / lip-sync.
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

    // Fast attack on rising target (mouth opens quickly), slow release on falling target (mouth closes gently). The clamp keeps the lerp factor frame-rate-correct at very large `delta` (e.g. after a tab pause): without it, the lerp would overshoot `target` and oscillate.
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
