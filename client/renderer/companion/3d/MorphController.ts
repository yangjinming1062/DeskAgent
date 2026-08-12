import * as THREE from 'three'

import type { SpriteEmotion } from '@/companion/companion-store'

/** Canonical semantic name → possible morph target names across model formats
 * (ARKit blendshapes, VRM expressions, Daz morphs, custom). The first alias
 * that exists in the loaded model's dictionary wins. */
const ALIASES: Record<string, string[]> = {
  blinkL: ['blink', 'eyeBlinkLeft', 'Blink_Left', 'blink_l', 'eye_blink_left'],
  blinkR: ['blink', 'eyeBlinkRight', 'Blink_Right', 'blink_r', 'eye_blink_right'],
  blink: ['blink', 'eyeBlink', 'eyesClosed', 'Blink', 'eye_blink'],
  smile: ['mouthSmile', 'mouthSmileLeft', 'mouthSmile', 'Smile', 'smile', 'mouth_smile_left', 'happy'],
  smileR: ['mouthSmileRight', 'Smile_R', 'mouth_smile_right', 'happy'],
  frown: ['mouthFrown', 'mouthFrownLeft', 'Frown', 'frown', 'sad', 'mouth_frown_left'],
  jawOpen: ['jawOpen', 'mouthOpen', 'Open', 'aa', 'mouth_open'],
  browUp: ['browInnerUp', 'browUp', 'BrowRaise', 'brow_up'],
  browDown: ['browInnerDown', 'browDown', 'BrowLower', 'brow_down'],
  eyeWide: ['eyeWideLeft', 'eyeWide', 'EyeWide', 'eye_wide'],
  eyeSquint: ['eyeSquintLeft', 'eyeSquint', 'Squint', 'eye_squint'],
  cheekRaise: ['cheekSquintLeft', 'cheekRaise', 'CheekRaise', 'cheek_raise'],
  eyelidDroop: ['eyesLookDown', 'eyelidDroop', 'eyeSquint', 'squint'],
  tongueOut: ['tongueOut', 'TongueOut', 'tongue_out']
}

/** Emotion → weighted semantic morph influences. Missing morphs are
 * silently skipped — only morphs present in the model contribute. */
const EMOTION_PRESETS: Record<string, Record<string, number>> = {
  happy: { smile: 0.8, cheekRaise: 0.4, eyeSquint: 0.3 },
  sad: { frown: 0.6, browDown: 0.5, eyelidDroop: 0.3 },
  surprised: { jawOpen: 0.3, eyeWide: 0.7, browUp: 0.6 },
  excited: { smile: 0.9, eyeWide: 0.5, browUp: 0.5, cheekRaise: 0.5 },
  confused: { browDown: 0.3, browUp: 0.2 },
  concerned: { frown: 0.3, browDown: 0.4 },
  shy: { smile: 0.3, eyelidDroop: 0.4, cheekRaise: 0.3 },
  proud: { smile: 0.5, browUp: 0.3 },
  grateful: { smile: 0.7, cheekRaise: 0.3 },
  playful: { smile: 0.6, eyeSquint: 0.4, tongueOut: 0.3 },
  bored: { frown: 0.3, eyelidDroop: 0.5 },
  lonely: { frown: 0.4, eyelidDroop: 0.3, browDown: 0.3 },
  sleepy: { eyelidDroop: 0.7, jawOpen: 0.1, browDown: 0.2 },
  curious: { browUp: 0.4, smile: 0.2 },
  embarrassed: { cheekRaise: 0.5, eyelidDroop: 0.3, smile: 0.2 },
  apologetic: { frown: 0.4, browDown: 0.4, eyelidDroop: 0.2 }
}

export class MorphController {
  private meshes: THREE.Mesh[] = []
  private resolved: Record<string, [number, number][]> = {}
  /** Cached at discovery time to avoid per-frame allocations. */
  private resolvedEntries: [string, [number, number][]][] = []
  private customPresets: Record<string, Record<string, number>> = {}
  private blinkHits: [number, number][] = []
  private targets: Record<string, number> = {}
  private blinkTimer = 0
  private blinkInterval = 3 + Math.random() * 3
  private blinkPhase: 'idle' | 'closing' | 'opening' = 'idle'
  private blinkElapsed = 0
  private lipSyncAmplitude = 0

  setCustomExpressions(exprs: readonly { name: string; weights: Record<string, number> }[]): void {
    this.customPresets = {}

    for (const expr of exprs) {
      if (expr.name && expr.weights) {
        this.customPresets[expr.name.toLowerCase()] = expr.weights
      }
    }
  }

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

    for (const [semantic, aliases] of Object.entries(ALIASES)) {
      const hits: [number, number][] = []

      for (let mi = 0; mi < this.meshes.length; mi++) {
        const dict = this.meshes[mi].morphTargetDictionary!

        for (const alias of aliases) {
          if (alias in dict) {
            hits.push([mi, dict[alias]])

            break
          }
        }
      }

      if (hits.length > 0) {
        this.resolved[semantic] = hits
      }
    }

    this.resolvedEntries = Object.entries(this.resolved)
    this.blinkHits = ['blink', 'blinkL', 'blinkR'].flatMap(k => this.resolved[k] ?? [])
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

  setExpression(emotion: SpriteEmotion | null): void {
    if (!emotion) {
      this.targets = {}

      return
    }

    const key = emotion.toLowerCase()
    const preset = this.customPresets[key] ?? EMOTION_PRESETS[key]

    if (preset) {
      this.targets = {}

      for (const [semantic, weight] of Object.entries(preset)) {
        if (semantic in this.resolved) {
          this.targets[semantic] = weight
        }
      }
    }
  }

  update(delta: number): void {
    if (this.meshes.length === 0) {
      return
    }

    const speed = Math.min(1, 8 * delta)

    for (const [semantic, hits] of this.resolvedEntries) {
      const target = this.targets[semantic] ?? 0

      for (const [mi, ti] of hits) {
        const infls = this.meshes[mi].morphTargetInfluences!

        if (infls[ti] !== undefined) {
          infls[ti] = THREE.MathUtils.lerp(infls[ti], target, speed)
        }
      }
    }

    this.updateBlink(delta)

    if (this.lipSyncAmplitude > 0.01) {
      const jawHits = this.resolved['jawOpen']

      if (jawHits) {
        for (const [mi, ti] of jawHits) {
          const infls = this.meshes[mi].morphTargetInfluences!

          if (infls[ti] !== undefined) {
            infls[ti] = Math.max(infls[ti], this.lipSyncAmplitude * 0.6)
          }
        }
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
