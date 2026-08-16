import * as THREE from 'three'

export interface Keyframe {
  t: number
  r: readonly [number, number, number]
}

export interface ClipDef {
  name: string
  duration: number
  loop: boolean
  category:
    | 'state'
    | 'micro'
    | 'context'
    | 'locomotion'
    | 'interaction'
    | 'ritual'
    | 'emotion-positive'
    | 'emotion-negative'
    | 'social'
    | 'intimate'
    | 'private'
    | 'daily'
    | 'surprise'
    | 'comfort'
    | 'weather'
    | 'neg-ext'
    | 'intim-ext'
    | 'music'
  tags?: readonly string[]
  tracks: Readonly<Record<string, ReadonlyArray<Keyframe>>>
}

const _QUAT = new THREE.Quaternion()
const _EULER = new THREE.Euler()

function _q(x: number, y: number, z: number): readonly [number, number, number, number] {
  _EULER.set(x, y, z, 'XYZ')
  _QUAT.setFromEuler(_EULER)

  return [_QUAT.x, _QUAT.y, _QUAT.z, _QUAT.w] as const
}

export function buildClip(def: ClipDef): THREE.AnimationClip {
  const tracks: THREE.QuaternionKeyframeTrack[] = []

  for (const [bone, kfs] of Object.entries(def.tracks)) {
    if (kfs.length < 2) {
      continue
    }

    const times: number[] = []
    const values: number[] = []

    for (const kf of kfs) {
      times.push(kf.t)
      const q = _q(...kf.r)
      values.push(q[0], q[1], q[2], q[3])
    }

    tracks.push(new THREE.QuaternionKeyframeTrack(`${bone}.quaternion`, times, values))
  }

  return new THREE.AnimationClip(def.name, def.duration, tracks)
}

function kf(t: number, x: number, y: number, z: number): Keyframe {
  return { t, r: [x, y, z] as const }
}

/** Canonical §3.1 state clips for a non-biped rig (AnimationMap resolves
 * states by these exact names, so every rig library must provide them).
 * Interim spine + head placeholder motion until per-rig keyframes land. */
export function buildStateClipsForBones(spine: string, head: string): Readonly<Record<string, ClipDef>> {
  const state = (
    name: string,
    duration: number,
    loop: boolean,
    spineMid: readonly [number, number, number],
    headMid: readonly [number, number, number]
  ): ClipDef => ({
    name,
    duration,
    loop,
    category: 'state',
    tracks: {
      [spine]: [kf(0, 0, 0, 0), kf(duration / 2, ...spineMid), kf(duration, 0, 0, 0)],
      [head]: [kf(0, 0, 0, 0), kf(duration / 2, ...headMid), kf(duration, 0, 0, 0)]
    }
  })

  return {
    idle: state('idle', 4, true, [0.02, 0, 0], [0.02, 0.03, 0]),
    listening: state('listening', 3, true, [0.01, 0, 0], [0.04, 0.06, 0]),
    thinking: state('thinking', 4, true, [0.02, 0, 0], [0.05, 0, 0]),
    speaking: state('speaking', 3.5, true, [0.02, 0, 0], [0.02, 0.02, 0]),
    working: state('working', 3.5, true, [0.03, 0, 0], [0.04, 0.02, 0]),
    sleeping: state('sleeping', 6, true, [-0.03, 0, 0], [0.14, 0, 0]),
    interacting: state('interacting', 1.5, false, [-0.05, 0, 0], [0.1, 0.06, 0]),
    emotional_idle: state('emotional_idle', 3.5, true, [0.02, 0, 0], [0.01, 0.02, 0]),
    disconnected: state('disconnected', 5, true, [-0.01, 0, 0], [0.07, 0.04, 0])
  }
}

// Helper for "breathing-only" placeholder clips — subtle Spine/Spine1/Head
// motion gives the model a live idle even before full keyframe design runs.
// Full keyframes replace these as LLM design passes land per spec §2.
function _placeholder(
  name: string,
  duration: number,
  loop: boolean,
  category: ClipDef['category'],
  tags?: readonly string[]
): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    ...(tags ? { tags } : {}),
    tracks: {
      Spine: [kf(0, 0, 0, 0), kf(duration / 2, -0.02, 0, 0), kf(duration, 0, 0, 0)],
      Spine1: [kf(0, 0, 0, 0), kf(duration / 2, 0, 0, 0.04), kf(duration, 0, 0, 0)],
      Head: [
        kf(0, 0, 0, 0),
        kf(duration / 4, 0.02, 0.03, 0),
        kf(duration / 2, 0, 0, 0),
        kf((3 * duration) / 4, -0.02, -0.03, 0),
        kf(duration, 0, 0, 0)
      ]
    }
  }
}

const _SPINE = 'Spine'
const _SPINE1 = 'Spine1'
const _NECK = 'Neck'
const _HEAD = 'Head'
const _LEFT_ARM = 'LeftArm'
const _LEFT_FORE = 'LeftForeArm'
const _RIGHT_ARM = 'RightArm'
const _RIGHT_FORE = 'RightForeArm'
const _LEFT_UP = 'LeftUpLeg'
const _LEFT_LEG = 'LeftLeg'
const _RIGHT_UP = 'RightUpLeg'
const _RIGHT_LEG = 'RightLeg'

export const BIPED_CLIPS: Readonly<Record<string, ClipDef>> = {
  // ── §3.1 core states (MUST 9) ──────────────────────────────────
  idle: {
    name: 'idle',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(2, 0.04, 0, 0), kf(4, 0, 0, 0)],
      [_SPINE1]: [
        kf(0, 0, 0, 0.02),
        kf(1, 0.02, 0.02, 0.03),
        kf(2, 0, 0, 0.01),
        kf(3, -0.02, -0.02, -0.01),
        kf(4, 0, 0, 0.02)
      ],
      [_HEAD]: [kf(0, 0, 0.04, 0), kf(2, 0.05, -0.04, 0), kf(4, 0, 0.04, 0)],
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 1.22), kf(2, 0.08, 0.03, 1.26), kf(4, 0.05, 0.05, 1.22)],
      [_LEFT_FORE]: [kf(0, 0.15, 0, 0.1), kf(2, 0.22, 0, 0.12), kf(4, 0.15, 0, 0.1)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, -1.22), kf(2, 0.08, -0.03, -1.26), kf(4, 0.05, -0.05, -1.22)],
      [_RIGHT_FORE]: [kf(0, 0.15, 0, -0.1), kf(2, 0.22, 0, -0.12), kf(4, 0.15, 0, -0.1)],
      [_LEFT_UP]: [kf(0, 0, 0, -0.03), kf(2, 0.02, 0, -0.02), kf(4, 0, 0, -0.03)],
      [_RIGHT_UP]: [kf(0, 0, 0, 0.03), kf(2, -0.02, 0, 0.02), kf(4, 0, 0, 0.03)]
    }
  },
  listening: {
    name: 'listening',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.08, 0.12, 0), kf(1.75, 0.06, 0.15, 0), kf(3.5, 0.08, 0.12, 0)],
      [_SPINE]: [kf(0, -0.04, 0, 0), kf(1.75, -0.03, 0, 0), kf(3.5, -0.04, 0, 0)],
      [_NECK]: [kf(0, -0.05, 0, 0), kf(1.75, -0.03, 0, 0), kf(3.5, -0.05, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.06, 0.06, 1.2), kf(1.75, 0.08, 0.08, 1.22), kf(3.5, 0.06, 0.06, 1.2)],
      [_LEFT_FORE]: [kf(0, 0.18, 0, 0.12), kf(1.75, 0.22, 0, 0.14), kf(3.5, 0.18, 0, 0.12)],
      [_RIGHT_ARM]: [kf(0, 0.06, -0.06, -1.2), kf(1.75, 0.08, -0.08, -1.22), kf(3.5, 0.06, -0.06, -1.2)],
      [_RIGHT_FORE]: [kf(0, 0.18, 0, -0.12), kf(1.75, 0.22, 0, -0.14), kf(3.5, 0.18, 0, -0.12)]
    }
  },
  thinking: {
    name: 'thinking',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 1.22), kf(2, 0.08, 0.03, 1.25), kf(4, 0.05, 0.05, 1.22)],
      [_LEFT_FORE]: [kf(0, 0.15, 0, 0.1), kf(2, 0.2, 0, 0.12), kf(4, 0.15, 0, 0.1)],
      [_RIGHT_ARM]: [kf(0, -1.2, 0, 0.4), kf(2, -1.1, 0, 0.5), kf(4, -1.2, 0, 0.4)],
      [_RIGHT_FORE]: [kf(0, 0.5, 0, 0), kf(2, 0.6, 0, 0), kf(4, 0.5, 0, 0)],
      [_HEAD]: [kf(0, -0.08, 0.06, 0), kf(1, -0.12, 0.08, 0), kf(2.5, -0.06, 0.08, 0), kf(4, -0.08, 0.06, 0)]
    }
  },
  speaking: {
    name: 'speaking',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_LEFT_ARM]: [
        kf(0, 0.1, 0.2, 0.95),
        kf(1, 0.25, 0.35, 0.8),
        kf(2, 0.12, 0.15, 0.9),
        kf(3, 0.2, 0.3, 0.85),
        kf(4, 0.1, 0.2, 0.95)
      ],
      [_LEFT_FORE]: [
        kf(0, 0.3, 0, 0.2),
        kf(1, 0.5, 0, 0.3),
        kf(2, 0.35, 0, 0.2),
        kf(3, 0.45, 0, 0.25),
        kf(4, 0.3, 0, 0.2)
      ],
      [_RIGHT_ARM]: [
        kf(0, 0.1, -0.2, -0.95),
        kf(1.5, 0.25, -0.35, -0.8),
        kf(3, 0.15, -0.18, -0.9),
        kf(4, 0.1, -0.2, -0.95)
      ],
      [_RIGHT_FORE]: [kf(0, 0.3, 0, -0.2), kf(1.5, 0.5, 0, -0.3), kf(3, 0.35, 0, -0.2), kf(4, 0.3, 0, -0.2)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(1, 0.04, 0.05, 0), kf(2, -0.02, 0, 0), kf(3, 0.03, -0.04, 0), kf(4, 0, 0, 0)],
      [_SPINE]: [kf(0, 0, 0, 0), kf(2, 0.03, 0, 0), kf(4, 0, 0, 0)]
    }
  },
  working: {
    name: 'working',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_LEFT_ARM]: [
        kf(0, 0.4, 0.5, 0.6),
        kf(0.5, 0.5, 0.4, 0.65),
        kf(1, 0.4, 0.5, 0.6),
        kf(1.5, 0.5, 0.4, 0.65),
        kf(2, 0.4, 0.5, 0.6),
        kf(2.5, 0.5, 0.4, 0.65),
        kf(3, 0.4, 0.5, 0.6),
        kf(3.5, 0.5, 0.4, 0.65)
      ],
      [_LEFT_FORE]: [kf(0, 0.6, 0, 0.2), kf(1.5, 0.7, 0, 0.25), kf(3.5, 0.6, 0, 0.2)],
      [_RIGHT_ARM]: [
        kf(0, 0.5, -0.4, -0.6),
        kf(0.5, 0.4, -0.5, -0.65),
        kf(1, 0.5, -0.4, -0.6),
        kf(1.5, 0.4, -0.5, -0.65),
        kf(2, 0.5, -0.4, -0.6),
        kf(2.5, 0.4, -0.5, -0.65),
        kf(3, 0.5, -0.4, -0.6),
        kf(3.5, 0.4, -0.5, -0.65)
      ],
      [_RIGHT_FORE]: [kf(0, 0.6, 0, -0.2), kf(1.5, 0.7, 0, -0.25), kf(3.5, 0.6, 0, -0.2)],
      [_SPINE]: [kf(0, -0.05, 0, 0), kf(1.75, -0.04, 0, 0), kf(3.5, -0.05, 0, 0)]
    }
  },
  sleeping: {
    name: 'sleeping',
    duration: 6,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.5, 0, 0), kf(3, 0.55, 0.03, 0), kf(6, 0.5, 0, 0)],
      [_SPINE]: [kf(0, -0.04, 0, 0), kf(3, -0.05, 0, 0), kf(6, -0.04, 0, 0)],
      [_SPINE1]: [kf(0, 0.12, 0, 0), kf(3, 0.16, 0, 0), kf(6, 0.12, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 1.25), kf(3, 0.06, 0.04, 1.28), kf(6, 0.05, 0.05, 1.25)],
      [_LEFT_FORE]: [kf(0, 0.2, 0, 0.1), kf(3, 0.22, 0, 0.1), kf(6, 0.2, 0, 0.1)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, -1.25), kf(3, 0.06, -0.04, -1.28), kf(6, 0.05, -0.05, -1.25)],
      [_RIGHT_FORE]: [kf(0, 0.2, 0, -0.1), kf(3, 0.22, 0, -0.1), kf(6, 0.2, 0, -0.1)]
    }
  },
  interacting: {
    name: 'interacting',
    duration: 1.5,
    loop: false,
    category: 'state',
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.15, -0.06, 0, 0), kf(0.6, 0, 0, 0), kf(1.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.3, 0.12, 0.25, 0), kf(1.5, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 1.22), kf(0.3, -0.1, 0.1, 1.0), kf(1.5, 0.05, 0.05, 1.22)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, -1.22), kf(0.3, -0.1, -0.1, -1.0), kf(1.5, 0.05, -0.05, -1.22)]
    }
  },
  emotional_idle: {
    name: 'emotional_idle',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_SPINE1]: [kf(0, 0, 0, 0), kf(1.75, 0, 0, 0.03), kf(3.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(1.75, 0.04, 0.03, 0), kf(3.5, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 1.22), kf(1.75, 0.08, 0.03, 1.25), kf(3.5, 0.05, 0.05, 1.22)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, -1.22), kf(1.75, 0.08, -0.03, -1.25), kf(3.5, 0.05, -0.05, -1.22)]
    }
  },
  disconnected: {
    name: 'disconnected',
    duration: 5,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.25, 0.15, 0), kf(2.5, 0.32, 0.18, 0), kf(5, 0.25, 0.15, 0)],
      [_SPINE]: [kf(0, -0.02, 0, 0), kf(2.5, -0.03, 0, 0), kf(5, -0.02, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 1.25), kf(2.5, 0.06, 0.04, 1.28), kf(5, 0.05, 0.05, 1.25)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, -1.25), kf(2.5, 0.06, -0.04, -1.28), kf(5, 0.05, -0.05, -1.25)]
    }
  },
  idle_look_around: {
    name: 'idle_look_around',
    duration: 2.5,
    loop: false,
    category: 'micro',
    tags: ['好奇', '灵动', '活泼'],
    tracks: {
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.5, 0, 0.3, 0.05), kf(1.25, 0, 0, 0), kf(1.75, 0, -0.3, -0.05), kf(2.5, 0, 0, 0)],
      [_NECK]: [kf(0, 0, 0, 0), kf(0.5, 0, 0.15, 0), kf(1.25, 0, 0, 0), kf(1.75, 0, -0.15, 0), kf(2.5, 0, 0, 0)],
      [_SPINE1]: [kf(0, 0, 0, 0), kf(0.5, 0, 0.05, 0), kf(1.25, 0, 0, 0), kf(1.75, 0, -0.05, 0), kf(2.5, 0, 0, 0)]
    }
  },
  idle_blink: {
    name: 'idle_blink',
    duration: 0.5,
    loop: false,
    category: 'micro',
    tags: ['呆萌', '软萌', '文静'],
    tracks: { [_HEAD]: [kf(0, 0, 0, 0), kf(0.15, 0.08, 0, 0), kf(0.3, 0.1, 0, 0), kf(0.5, 0, 0, 0)] }
  },
  idle_stretch: {
    name: 'idle_stretch',
    duration: 2.5,
    loop: false,
    category: 'micro',
    tags: ['慵懒', '随和', '阳光'],
    tracks: {
      [_LEFT_ARM]: [kf(0, 0, 0, 0.1), kf(0.5, -0.3, 0, 0.8), kf(1.5, -0.2, 0, 0.6), kf(2.5, 0, 0, 0.1)],
      [_RIGHT_ARM]: [kf(0, 0, 0, -0.1), kf(0.5, -0.3, 0, -0.8), kf(1.5, -0.2, 0, -0.6), kf(2.5, 0, 0, -0.1)],
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.5, -0.04, 0, 0), kf(1.5, -0.02, 0, 0), kf(2.5, 0, 0, 0)]
    }
  },
  idle_shift_weight: {
    name: 'idle_shift_weight',
    duration: 1.5,
    loop: false,
    category: 'micro',
    tags: ['沉稳', '冷静', '随和'],
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, 0.03), kf(1.1, 0, 0, -0.02), kf(1.5, 0, 0, 0)],
      [_LEFT_UP]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, -0.02), kf(1.1, 0, 0, 0.01), kf(1.5, 0, 0, 0)],
      [_RIGHT_UP]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, 0.02), kf(1.1, 0, 0, -0.01), kf(1.5, 0, 0, 0)]
    }
  },
  idle_yawn: _placeholder('idle_yawn', 2.5, false, 'micro', ['慵懒', '呆萌']),
  idle_fidget: _placeholder('idle_fidget', 1.5, false, 'micro', ['调皮', '好动', '元气']),
  idle_humming: _placeholder('idle_humming', 4, true, 'context', ['阳光', '温柔', '轻快活泼']),
  idle_dreamy: _placeholder('idle_dreamy', 5, true, 'context', ['文静', '仙气', '多愁善感']),
  idle_typing: _placeholder('idle_typing', 4, true, 'context', ['严谨', '理性', '博学']),
  idle_bounce: _placeholder('idle_bounce', 3, true, 'context', ['活泼', '元气', '开朗']),
  idle_calm: _placeholder('idle_calm', 5, true, 'context', ['冷静', '沉稳', '高冷']),
  idle_engaged: _placeholder('idle_engaged', 3.5, true, 'context', ['聪明', '严谨', '知性']),
  walk: {
    name: 'walk',
    duration: 1.2,
    loop: true,
    category: 'locomotion',
    tags: ['活泼', '阳光', '开朗'],
    tracks: {
      [_LEFT_UP]: [kf(0, 0.25, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.25, 0, 0)],
      [_LEFT_LEG]: [kf(0, -0.1, 0, 0), kf(0.6, 0.15, 0, 0), kf(1.2, -0.1, 0, 0)],
      [_RIGHT_UP]: [kf(0, -0.2, 0, 0), kf(0.6, 0.25, 0, 0), kf(1.2, -0.2, 0, 0)],
      [_RIGHT_LEG]: [kf(0, 0.15, 0, 0), kf(0.6, -0.1, 0, 0), kf(1.2, 0.15, 0, 0)],
      [_LEFT_ARM]: [kf(0, -0.15, 0, 0), kf(0.6, 0.15, 0, 0), kf(1.2, -0.15, 0, 0)],
      [_RIGHT_ARM]: [kf(0, 0.15, 0, 0), kf(0.6, -0.15, 0, 0), kf(1.2, 0.15, 0, 0)],
      [_SPINE]: [kf(0, -0.04, 0, 0), kf(0.6, -0.02, 0, 0), kf(1.2, -0.04, 0, 0)]
    }
  },
  idle_to_walk: _placeholder('idle_to_walk', 0.5, false, 'locomotion', ['灵动']),
  walk_to_idle: _placeholder('walk_to_idle', 0.5, false, 'locomotion', ['沉稳']),
  fly: {
    name: 'fly',
    duration: 2.5,
    loop: true,
    category: 'locomotion',
    tags: ['仙气', '灵动', '优雅'],
    tracks: {
      [_LEFT_ARM]: [kf(0, 0, 0, 0), kf(0.6, -0.6, 0, 0), kf(1.25, 0, 0, 0), kf(1.85, -0.6, 0, 0), kf(2.5, 0, 0, 0)],
      [_RIGHT_ARM]: [kf(0, 0, 0, 0), kf(0.6, -0.6, 0, 0), kf(1.25, 0, 0, 0), kf(1.85, -0.6, 0, 0), kf(2.5, 0, 0, 0)],
      [_LEFT_UP]: [kf(0, -0.15, 0, 0), kf(1.25, -0.2, 0, 0), kf(2.5, -0.15, 0, 0)],
      [_RIGHT_UP]: [kf(0, -0.15, 0, 0), kf(1.25, -0.2, 0, 0), kf(2.5, -0.15, 0, 0)]
    }
  },
  drag: {
    name: 'drag',
    duration: 1.6,
    loop: true,
    category: 'locomotion',
    tags: ['调皮', '可爱'],
    tracks: {
      [_HEAD]: [kf(0, -0.22, 0.05, 0), kf(0.8, -0.28, -0.05, 0), kf(1.6, -0.22, 0.05, 0)],
      [_SPINE]: [kf(0, 0.08, 0, 0.03), kf(0.8, 0.05, 0, -0.03), kf(1.6, 0.08, 0, 0.03)],
      [_SPINE1]: [kf(0, 0.05, 0, 0.02), kf(0.8, 0.08, 0, -0.02), kf(1.6, 0.05, 0, 0.02)],
      [_LEFT_ARM]: [kf(0, 0.15, 0.25, 0.75), kf(0.8, 0.3, 0.1, 0.6), kf(1.6, 0.15, 0.25, 0.75)],
      [_LEFT_FORE]: [kf(0, 0.35, 0, 0.25), kf(0.8, 0.2, 0, 0.15), kf(1.6, 0.35, 0, 0.25)],
      [_RIGHT_ARM]: [kf(0, 0.15, -0.25, -0.75), kf(0.8, 0.3, -0.1, -0.6), kf(1.6, 0.15, -0.25, -0.75)],
      [_RIGHT_FORE]: [kf(0, 0.35, 0, -0.25), kf(0.8, 0.2, 0, -0.15), kf(1.6, 0.35, 0, -0.25)],
      [_LEFT_UP]: [kf(0, 0.28, 0.05, -0.08), kf(0.8, -0.18, -0.05, -0.04), kf(1.6, 0.28, 0.05, -0.08)],
      [_LEFT_LEG]: [kf(0, 0.35, 0, 0), kf(0.8, 0.12, 0, 0), kf(1.6, 0.35, 0, 0)],
      [_RIGHT_UP]: [kf(0, -0.18, -0.05, 0.04), kf(0.8, 0.28, 0.05, 0.08), kf(1.6, -0.18, -0.05, 0.04)],
      [_RIGHT_LEG]: [kf(0, 0.12, 0, 0), kf(0.8, 0.35, 0, 0), kf(1.6, 0.12, 0, 0)]
    }
  },
  poke_light: {
    name: 'poke_light',
    duration: 0.8,
    loop: false,
    category: 'interaction',
    tags: ['温柔', '体贴', '随和', '温婉'],
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.1, -0.03, 0, 0), kf(0.8, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.15, -0.05, 0.2, 0), kf(0.5, -0.03, 0.1, 0), kf(0.8, 0, 0, 0)]
    }
  },
  poke_heavy: {
    name: 'poke_heavy',
    duration: 1.2,
    loop: false,
    category: 'interaction',
    tags: ['傲娇', '毒舌', '强势', '叛逆'],
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.1, -0.08, 0, 0.03), kf(0.4, -0.04, 0, 0.01), kf(1.2, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.15, -0.1, 0.25, 0), kf(0.5, -0.06, 0.12, 0), kf(1.2, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0, 0, 0), kf(0.15, 0.1, 0, 0.2), kf(1.2, 0, 0, 0)]
    }
  },
  poke_happy: {
    name: 'poke_happy',
    duration: 1,
    loop: false,
    category: 'interaction',
    tags: ['活泼', '元气', '开朗', '俏皮'],
    tracks: {
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.15, -0.05, -0.2, 0), kf(0.5, -0.02, -0.1, 0), kf(1, 0, 0, 0)],
      [_RIGHT_ARM]: [kf(0, 0, 0, 0), kf(0.3, 0.2, 0, -0.3), kf(0.7, 0.1, 0, -0.15), kf(1, 0, 0, 0)]
    }
  },
  poke_angry: _placeholder('poke_angry', 1, false, 'interaction', ['暴躁', '冷漠', '强势']),
  poke_shy: _placeholder('poke_shy', 1, false, 'interaction', ['害羞', '社恐', '软萌', '温婉']),
  drag_end: {
    name: 'drag_end',
    duration: 0.8,
    loop: false,
    category: 'interaction',
    tags: ['温柔', '体贴', '活泼'],
    tracks: {
      [_SPINE]: [kf(0, 0.08, 0, 0), kf(0.25, -0.14, 0, 0), kf(0.55, -0.04, 0, 0), kf(0.8, 0, 0, 0)],
      [_HEAD]: [kf(0, -0.15, 0, 0), kf(0.25, 0.1, 0, 0), kf(0.55, 0.04, 0, 0), kf(0.8, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.15, 0.2, 0.75), kf(0.3, 0.1, 0.08, 1.15), kf(0.8, 0.05, 0.05, 1.22)],
      [_LEFT_FORE]: [kf(0, 0.35, 0, 0.25), kf(0.3, 0.25, 0, 0.15), kf(0.8, 0.15, 0, 0.1)],
      [_RIGHT_ARM]: [kf(0, 0.15, -0.2, -0.75), kf(0.3, 0.1, -0.08, -1.15), kf(0.8, 0.05, -0.05, -1.22)],
      [_RIGHT_FORE]: [kf(0, 0.35, 0, -0.25), kf(0.3, 0.25, 0, -0.15), kf(0.8, 0.15, 0, -0.1)],
      [_LEFT_UP]: [kf(0, 0.15, 0, 0), kf(0.25, 0.32, 0, 0), kf(0.55, 0.08, 0, 0), kf(0.8, 0, 0, 0)],
      [_LEFT_LEG]: [kf(0, 0.2, 0, 0), kf(0.25, 0.48, 0, 0), kf(0.55, 0.12, 0, 0), kf(0.8, 0, 0, 0)],
      [_RIGHT_UP]: [kf(0, 0.15, 0, 0), kf(0.25, 0.32, 0, 0), kf(0.55, 0.08, 0, 0), kf(0.8, 0, 0, 0)],
      [_RIGHT_LEG]: [kf(0, 0.2, 0, 0), kf(0.25, 0.48, 0, 0), kf(0.55, 0.12, 0, 0), kf(0.8, 0, 0, 0)]
    }
  },
  greeting: {
    name: 'greeting',
    duration: 2.5,
    loop: false,
    category: 'ritual',
    tags: ['温柔', '体贴', '随和', '阳光'],
    tracks: {
      [_RIGHT_ARM]: [kf(0, 0, 0, 0), kf(0.4, 0.3, -0.8, 0), kf(1.6, 0.3, -0.7, 0), kf(2.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(1.2, 0.06, 0, 0), kf(2.5, 0, 0, 0)]
    }
  },
  goodbye: {
    name: 'goodbye',
    duration: 2,
    loop: false,
    category: 'ritual',
    tags: ['温柔', '体贴', '体面'],
    tracks: {
      [_RIGHT_ARM]: [
        kf(0, 0, 0, 0),
        kf(0.3, 0.2, -0.6, 0),
        kf(0.8, 0.2, -0.5, 0.3),
        kf(1.2, 0.2, -0.5, -0.3),
        kf(1.6, 0.2, -0.5, 0.2),
        kf(2, 0, 0, 0)
      ],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.5, 0.04, -0.05, 0), kf(2, 0, 0, 0)]
    }
  },
  wake_up: _placeholder('wake_up', 2.5, false, 'ritual', ['慵懒', '软萌', '呆萌']),
  // ── §3.7 positive emotion (SHOULD 8) ───────────────────────────
  dance_happy: _placeholder('dance_happy', 2.5, false, 'emotion-positive', ['活泼', '元气', '阳光', '开朗']),
  celebrate: _placeholder('celebrate', 1.8, false, 'emotion-positive', ['热血', '元气', '阳光', '社牛']),
  giggle: _placeholder('giggle', 1.2, false, 'emotion-positive', ['俏皮', '调皮', '搞怪', '呆萌']),
  cheer: _placeholder('cheer', 1.3, false, 'emotion-positive', ['元气', '开朗', '好动']),
  clap: {
    name: 'clap',
    duration: 1.2,
    loop: false,
    category: 'emotion-positive',
    tags: ['温柔', '随和', '阳光', '知性'],
    tracks: {
      [_LEFT_ARM]: [
        kf(0, 0.5, 0.7, 0),
        kf(0.3, 0.4, 0.4, 0),
        kf(0.6, 0.5, 0.7, 0),
        kf(0.9, 0.4, 0.4, 0),
        kf(1.2, 0.5, 0.7, 0)
      ],
      [_RIGHT_ARM]: [
        kf(0, 0.5, -0.7, 0),
        kf(0.3, 0.4, -0.4, 0),
        kf(0.6, 0.5, -0.7, 0),
        kf(0.9, 0.4, -0.4, 0),
        kf(1.2, 0.5, -0.7, 0)
      ]
    }
  },
  spin_happy: _placeholder('spin_happy', 2, false, 'emotion-positive', ['活泼', '灵动', '仙气']),
  jump_joy: _placeholder('jump_joy', 1, false, 'emotion-positive', ['元气', '好动', '活泼']),
  heart_pose: _placeholder('heart_pose', 1.8, false, 'emotion-positive', ['软萌', '粘人', '俏皮', '妩媚']),
  // ── §3.8 negative emotion (SHOULD 8) ───────────────────────────
  pout: _placeholder('pout', 1.5, false, 'emotion-negative', ['傲娇', '俏皮', '粘人']),
  stomp_angry: _placeholder('stomp_angry', 1.5, false, 'emotion-negative', ['暴躁', '叛逆', '傲娇']),
  sulk: _placeholder('sulk', 3.5, true, 'emotion-negative', ['傲娇', '孤僻', '忧郁']),
  cry: _placeholder('cry', 2.5, false, 'emotion-negative', ['多愁善感', '软萌', '敏感']),
  tremble_fear: _placeholder('tremble_fear', 2.5, true, 'emotion-negative', ['胆小', '社恐', '害羞']),
  collapse_sad: _placeholder('collapse_sad', 2, false, 'emotion-negative', ['忧郁', '多愁善感', '敏感']),
  shake_frustration: _placeholder('shake_frustration', 1.5, false, 'emotion-negative', ['暴躁', '毒舌', '严谨']),
  withdrawal: _placeholder('withdrawal', 3.5, true, 'emotion-negative', ['孤僻', '高冷', '社恐']),
  // ── §3.9 social (SHOULD 6) ─────────────────────────────────────
  wave_warm: _placeholder('wave_warm', 1.8, false, 'social', ['温柔', '体贴', '阳光', '开朗']),
  bow: {
    name: 'bow',
    duration: 1.5,
    loop: false,
    category: 'social',
    tags: ['严谨', '体面', '优雅', '文静'],
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.7, 0.45, 0, 0), kf(1.5, 0, 0, 0)],
      [_SPINE1]: [kf(0, 0, 0, 0), kf(0.7, 0.2, 0, 0), kf(1.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.7, 0.15, 0, 0), kf(1.5, 0, 0, 0)]
    }
  },
  fold_arms: {
    name: 'fold_arms',
    duration: 2.5,
    loop: true,
    category: 'social',
    tags: ['傲娇', '高冷', '理性', '强势', '冷漠'],
    tracks: {
      [_LEFT_ARM]: [kf(0, 0.3, 0, 0.7), kf(1.25, 0.35, 0, 0.68), kf(2.5, 0.3, 0, 0.7)],
      [_LEFT_FORE]: [kf(0, -0.8, 0.3, 0), kf(1.25, -0.75, 0.3, 0), kf(2.5, -0.8, 0.3, 0)],
      [_RIGHT_ARM]: [kf(0, 0.3, 0, -0.7), kf(1.25, 0.35, 0, -0.68), kf(2.5, 0.3, 0, -0.7)],
      [_RIGHT_FORE]: [kf(0, -0.8, -0.3, 0), kf(1.25, -0.75, -0.3, 0), kf(2.5, -0.8, -0.3, 0)]
    }
  },
  standing_relax: {
    name: 'standing_relax',
    duration: 2.5,
    loop: true,
    category: 'social',
    tags: ['随和', '慵懒', '冷静', '从容不迫'],
    tracks: {
      [_SPINE]: [kf(0, 0.02, 0, 0), kf(1.25, 0.01, 0, 0.01), kf(2.5, 0.02, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(1.25, 0.01, 0.02, 0), kf(2.5, 0, 0, 0)]
    }
  },
  shrug: {
    name: 'shrug',
    duration: 1,
    loop: false,
    category: 'social',
    tags: ['幽默', '调皮', '随和', '腹黑'],
    tracks: {
      [_LEFT_ARM]: [kf(0, 0, 0, 0.1), kf(0.3, 0.15, 0, 0.3), kf(0.6, 0.15, 0, 0.3), kf(1, 0, 0, 0.1)],
      [_RIGHT_ARM]: [kf(0, 0, 0, -0.1), kf(0.3, 0.15, 0, -0.3), kf(0.6, 0.15, 0, -0.3), kf(1, 0, 0, -0.1)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.3, -0.05, 0, 0), kf(1, 0, 0, 0)]
    }
  },
  shake_head: {
    name: 'shake_head',
    duration: 1,
    loop: false,
    category: 'social',
    tags: ['理性', '冷静', '严肃', '知性'],
    tracks: {
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.2, 0, 0.2, 0), kf(0.5, 0, -0.2, 0), kf(0.8, 0, 0.1, 0), kf(1, 0, 0, 0)]
    }
  },
  // ── §3.10 intimate (SHOULD 10) ─────────────────────────────────
  hug_offer: _placeholder('hug_offer', 2, false, 'intimate', ['温柔', '暖心', '体贴', '粘人']),
  hug_receive: _placeholder('hug_receive', 2, false, 'intimate', ['温柔', '软萌', '粘人']),
  kiss_lips: _placeholder('kiss_lips', 1.5, false, 'intimate', ['深情', '妩媚', '妖娆', '温柔']),
  kiss_cheek: _placeholder('kiss_cheek', 1.5, false, 'intimate', ['俏皮', '可爱', '体贴']),
  lap_pillow: _placeholder('lap_pillow', 3.5, true, 'intimate', ['温婉', '体贴', '母性', '温柔']),
  lean_on_shoulder: _placeholder('lean_on_shoulder', 3.5, true, 'intimate', ['粘人', '害羞', '文静']),
  whisper: _placeholder('whisper', 1.5, false, 'intimate', ['腹黑', '俏皮', '神秘']),
  cuddle: _placeholder('cuddle', 3.5, true, 'intimate', ['粘人', '软萌', '温顺']),
  hold_hand: _placeholder('hold_hand', 2.5, true, 'intimate', ['温暖', '忠诚', '体贴']),
  pat_receive: _placeholder('pat_receive', 1.5, false, 'intimate', ['乖巧', '软萌', '温顺']),
  // ── §3.11 private (SHOULD 5) ───────────────────────────────────
  intimate_embrace: _placeholder('intimate_embrace', 3.5, true, 'private', ['深情', '温柔', '妩媚']),
  sleep_together: _placeholder('sleep_together', 5, true, 'private', ['温情', '安详', '体贴']),
  carry_princess: _placeholder('carry_princess', 3.5, true, 'private', ['霸道', '强势', '热血']),
  forehead_touch: _placeholder('forehead_touch', 2, false, 'private', ['细腻', '深情', '温柔']),
  nuzzle: _placeholder('nuzzle', 2, false, 'private', ['撒娇', '粘人', '软萌']),
  // ── §3.12 daily (SHOULD 6) ─────────────────────────────────────
  sit: _placeholder('sit', 3.5, true, 'daily', ['安静', '文静', '随和']),
  eat: _placeholder('eat', 2.5, true, 'daily', ['贪吃', '呆萌', '活泼']),
  drink: _placeholder('drink', 2.5, true, 'daily', ['优雅', '从容不迫']),
  read: _placeholder('read', 3.5, true, 'daily', ['知性', '博学', '严谨']),
  pet_animal: _placeholder('pet_animal', 2.5, true, 'daily', ['温柔', '亲人', '爱心']),
  exercise_stretch: _placeholder('exercise_stretch', 3.5, true, 'daily', ['阳光', '元气', '好动']),
  // ── §3.13 surprise (SHOULD 7) ──────────────────────────────────
  surprise_jump: _placeholder('surprise_jump', 1, false, 'surprise', ['胆小', '敏锐', '活泼']),
  shock_stepback: _placeholder('shock_stepback', 1, false, 'surprise', ['敏感', '警惕']),
  dizzy: _placeholder('dizzy', 2.5, true, 'surprise', ['呆萌', '中二', '搞怪']),
  embarrassed_cover: _placeholder('embarrassed_cover', 1.5, false, 'surprise', ['害羞', '社恐', '软萌']),
  proud_pose: _placeholder('proud_pose', 1.5, false, 'surprise', ['傲娇', '贵气', '自信']),
  relieved_sigh: _placeholder('relieved_sigh', 1.5, false, 'surprise', ['随和', '从容']),
  curious_lean: _placeholder('curious_lean', 1.5, false, 'surprise', ['好奇', '灵动', '聪明']),
  // ── §3.15 comfort / healing (SHOULD 6) ─────────────────────────
  comfort_pat: _placeholder('comfort_pat', 1.5, false, 'comfort', ['温柔', '体贴', '暖心']),
  pat_head_give: _placeholder('pat_head_give', 2, false, 'comfort', ['温柔', '大度', '关爱']),
  wipe_tears: _placeholder('wipe_tears', 2, false, 'comfort', ['细腻', '体贴', '深情']),
  warm_smile: _placeholder('warm_smile', 1.5, false, 'comfort', ['温柔', '暖心', '治愈']),
  reassure_nod: _placeholder('reassure_nod', 1.5, false, 'comfort', ['沉稳', '忠诚', '可靠']),
  hug_comfort: _placeholder('hug_comfort', 2, false, 'comfort', ['暖心', '体贴', '温柔']),
  // ── §3.16 weather / environment (SHOULD 5) ─────────────────────
  shiver_cold: _placeholder('shiver_cold', 2.5, true, 'weather', ['脆弱', '惹人怜爱']),
  fan_self: _placeholder('fan_self', 2, false, 'weather', ['娇憨', '活泼']),
  sneeze: _placeholder('sneeze', 0.8, false, 'weather', ['呆萌', '可爱']),
  rain_look: _placeholder('rain_look', 1.5, false, 'weather', ['多愁善感', '文静', '忧郁']),
  sunbathe: _placeholder('sunbathe', 4, true, 'weather', ['惬意', '慵懒', '阳光']),
  // ── §3.17 negative emotion extension (SHOULD 5) ───────────────
  glare: _placeholder('glare', 1.5, false, 'neg-ext', ['高冷', '冷漠', '毒舌', '强势']),
  silent_treatment: _placeholder('silent_treatment', 3, true, 'neg-ext', ['傲娇', '冷漠', '孤僻']),
  disappointed_walk: _placeholder('disappointed_walk', 1.5, false, 'neg-ext', ['失落', '忧郁']),
  jealous_pout: _placeholder('jealous_pout', 1.5, false, 'neg-ext', ['吃醋', '傲娇', '占有欲']),
  envy_sigh: _placeholder('envy_sigh', 1.5, false, 'neg-ext', ['细腻', '多愁善感']),
  // ── §3.18 intimate extension (SHOULD 5) ────────────────────────
  forehead_kiss: _placeholder('forehead_kiss', 1.5, false, 'intim-ext', ['温柔', '珍视', '体贴']),
  nose_boop: _placeholder('nose_boop', 1, false, 'intim-ext', ['调皮', '俏皮', '亲密']),
  hand_kiss: _placeholder('hand_kiss', 1.5, false, 'intim-ext', ['优雅', '贵气', '忠诚']),
  spoon: _placeholder('spoon', 4, true, 'intim-ext', ['依偎', '温存', '粘人']),
  piggyback: _placeholder('piggyback', 3, true, 'intim-ext', ['活泼', '依赖', '元气']),
  // ── §3.19 music / dance extension (SHOULD 3) ──────────────────
  dance_sway: _placeholder('dance_sway', 3, true, 'music', ['优雅', '仙气', '妩媚', '妖娆']),
  dance_spin: _placeholder('dance_spin', 2, false, 'music', ['华丽', '灵动', '活泼']),
  conduct_music: _placeholder('conduct_music', 3, true, 'music', ['优雅', '严谨', '艺术'])
}
