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
function _placeholder(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
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
    duration: 5,
    loop: true,
    category: 'state',
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(2.5, 0.02, 0, 0), kf(5, 0, 0, 0)],
      [_SPINE1]: [kf(0, 0, 0, 0), kf(1.25, 0, 0, 0.03), kf(2.5, 0, 0, 0), kf(3.75, 0, 0, -0.02), kf(5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(2.5, 0.04, 0.05, 0), kf(5, 0, 0, 0)]
    }
  },
  listening: {
    name: 'listening',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.06, 0.08, 0), kf(1.75, 0.04, 0.1, 0), kf(3.5, 0.06, 0.08, 0)],
      [_SPINE]: [kf(0, -0.03, 0, 0), kf(1.75, -0.02, 0, 0), kf(3.5, -0.03, 0, 0)],
      [_NECK]: [kf(0, -0.04, 0, 0), kf(1.75, -0.03, 0, 0), kf(3.5, -0.04, 0, 0)]
    }
  },
  thinking: {
    name: 'thinking',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_RIGHT_ARM]: [kf(0, -1.2, 0, 0.4), kf(2, -1.1, 0, 0.5), kf(4, -1.2, 0, 0.4)],
      [_RIGHT_FORE]: [kf(0, 0.3, 0, 0), kf(2, 0.4, 0, 0), kf(4, 0.3, 0, 0)],
      [_HEAD]: [kf(0, -0.05, 0.04, 0), kf(1, -0.08, 0.05, 0), kf(2.5, -0.04, 0.06, 0), kf(4, -0.05, 0.04, 0)]
    }
  },
  speaking: {
    name: 'speaking',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_LEFT_ARM]: [
        kf(0, 0.2, 0.4, 0),
        kf(1, 0.3, 0.6, 0),
        kf(2, 0.15, 0.3, 0),
        kf(3, 0.25, 0.5, 0),
        kf(4, 0.2, 0.4, 0)
      ],
      [_RIGHT_ARM]: [kf(0, 0.2, -0.4, 0), kf(1.5, 0.3, -0.6, 0), kf(3, 0.18, -0.35, 0), kf(4, 0.2, -0.4, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(2, 0.03, 0.04, 0), kf(4, 0, 0, 0)]
    }
  },
  working: {
    name: 'working',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_LEFT_ARM]: [
        kf(0, 0.4, 0.5, 0),
        kf(0.5, 0.5, 0.4, 0),
        kf(1, 0.4, 0.5, 0),
        kf(1.5, 0.5, 0.4, 0),
        kf(2, 0.4, 0.5, 0),
        kf(2.5, 0.5, 0.4, 0),
        kf(3, 0.4, 0.5, 0),
        kf(3.5, 0.5, 0.4, 0)
      ],
      [_RIGHT_ARM]: [
        kf(0, 0.5, -0.4, 0),
        kf(0.5, 0.4, -0.5, 0),
        kf(1, 0.5, -0.4, 0),
        kf(1.5, 0.4, -0.5, 0),
        kf(2, 0.5, -0.4, 0),
        kf(2.5, 0.4, -0.5, 0),
        kf(3, 0.5, -0.4, 0),
        kf(3.5, 0.4, -0.5, 0)
      ],
      [_SPINE]: [kf(0, -0.05, 0, 0), kf(1.75, -0.04, 0, 0), kf(3.5, -0.05, 0, 0)]
    }
  },
  sleeping: {
    name: 'sleeping',
    duration: 6,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.6, 0, 0), kf(3, 0.65, 0.02, 0), kf(6, 0.6, 0, 0)],
      [_SPINE]: [kf(0, -0.04, 0, 0), kf(3, -0.05, 0, 0), kf(6, -0.04, 0, 0)],
      [_SPINE1]: [kf(0, 0.15, 0, 0), kf(3, 0.18, 0, 0), kf(6, 0.15, 0, 0)]
    }
  },
  interacting: {
    name: 'interacting',
    duration: 1.5,
    loop: false,
    category: 'state',
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.15, -0.04, 0, 0), kf(0.6, 0, 0, 0), kf(1.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.3, 0.1, 0.3, 0), kf(1.5, 0, 0, 0)]
    }
  },
  emotional_idle: {
    name: 'emotional_idle',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_SPINE1]: [kf(0, 0, 0, 0), kf(1.75, 0, 0, 0.03), kf(3.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(1.75, 0.02, 0, 0), kf(3.5, 0, 0, 0)]
    }
  },
  disconnected: {
    name: 'disconnected',
    duration: 5,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.25, 0.15, 0), kf(2.5, 0.32, 0.18, 0), kf(5, 0.25, 0.15, 0)],
      [_SPINE]: [kf(0, -0.02, 0, 0), kf(2.5, -0.03, 0, 0), kf(5, -0.02, 0, 0)]
    }
  },
  // ── §3.2 micro (SHOULD 6) ──────────────────────────────────────
  idle_look_around: {
    name: 'idle_look_around',
    duration: 2.5,
    loop: false,
    category: 'micro',
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
    tracks: { [_HEAD]: [kf(0, 0, 0, 0), kf(0.15, 0.08, 0, 0), kf(0.3, 0.1, 0, 0), kf(0.5, 0, 0, 0)] }
  },
  idle_stretch: {
    name: 'idle_stretch',
    duration: 2.5,
    loop: false,
    category: 'micro',
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
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, 0.03), kf(1.1, 0, 0, -0.02), kf(1.5, 0, 0, 0)],
      [_LEFT_UP]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, -0.02), kf(1.1, 0, 0, 0.01), kf(1.5, 0, 0, 0)],
      [_RIGHT_UP]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, 0.02), kf(1.1, 0, 0, -0.01), kf(1.5, 0, 0, 0)]
    }
  },
  idle_yawn: _placeholder('idle_yawn', 2.5, false, 'micro'),
  idle_fidget: _placeholder('idle_fidget', 1.5, false, 'micro'),
  // ── §3.3 context idle (SHOULD 6) ───────────────────────────────
  idle_humming: _placeholder('idle_humming', 4, true, 'context'),
  idle_dreamy: _placeholder('idle_dreamy', 5, true, 'context'),
  idle_typing: _placeholder('idle_typing', 4, true, 'context'),
  idle_bounce: _placeholder('idle_bounce', 3, true, 'context'),
  idle_calm: _placeholder('idle_calm', 5, true, 'context'),
  idle_engaged: _placeholder('idle_engaged', 3.5, true, 'context'),
  // ── §3.4 locomotion (MUST walk + SHOULD 4) ─────────────────────
  walk: {
    name: 'walk',
    duration: 1.2,
    loop: true,
    category: 'locomotion',
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
  idle_to_walk: _placeholder('idle_to_walk', 0.5, false, 'locomotion'),
  walk_to_idle: _placeholder('walk_to_idle', 0.5, false, 'locomotion'),
  fly: {
    name: 'fly',
    duration: 2.5,
    loop: true,
    category: 'locomotion',
    tracks: {
      [_LEFT_ARM]: [kf(0, 0, 0, 0), kf(0.6, -0.6, 0, 0), kf(1.25, 0, 0, 0), kf(1.85, -0.6, 0, 0), kf(2.5, 0, 0, 0)],
      [_RIGHT_ARM]: [kf(0, 0, 0, 0), kf(0.6, -0.6, 0, 0), kf(1.25, 0, 0, 0), kf(1.85, -0.6, 0, 0), kf(2.5, 0, 0, 0)],
      [_LEFT_UP]: [kf(0, -0.15, 0, 0), kf(1.25, -0.2, 0, 0), kf(2.5, -0.15, 0, 0)],
      [_RIGHT_UP]: [kf(0, -0.15, 0, 0), kf(1.25, -0.2, 0, 0), kf(2.5, -0.15, 0, 0)]
    }
  },
  drag: _placeholder('drag', 0.5, false, 'locomotion'),
  // ── §3.5 interaction (SHOULD 6) ────────────────────────────────
  poke_light: {
    name: 'poke_light',
    duration: 0.8,
    loop: false,
    category: 'interaction',
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
    tracks: {
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.15, -0.05, -0.2, 0), kf(0.5, -0.02, -0.1, 0), kf(1, 0, 0, 0)],
      [_RIGHT_ARM]: [kf(0, 0, 0, 0), kf(0.3, 0.2, 0, -0.3), kf(0.7, 0.1, 0, -0.15), kf(1, 0, 0, 0)]
    }
  },
  poke_angry: _placeholder('poke_angry', 1, false, 'interaction'),
  poke_shy: _placeholder('poke_shy', 1, false, 'interaction'),
  drag_end: _placeholder('drag_end', 0.8, false, 'interaction'),
  // ── §3.6 ritual (SHOULD 3) ─────────────────────────────────────
  greeting: {
    name: 'greeting',
    duration: 2.5,
    loop: false,
    category: 'ritual',
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
  wake_up: _placeholder('wake_up', 2.5, false, 'ritual'),
  // ── §3.7 positive emotion (SHOULD 8) ───────────────────────────
  dance_happy: _placeholder('dance_happy', 2.5, false, 'emotion-positive'),
  celebrate: _placeholder('celebrate', 1.8, false, 'emotion-positive'),
  giggle: _placeholder('giggle', 1.2, false, 'emotion-positive'),
  cheer: _placeholder('cheer', 1.3, false, 'emotion-positive'),
  clap: {
    name: 'clap',
    duration: 1.2,
    loop: false,
    category: 'emotion-positive',
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
  spin_happy: _placeholder('spin_happy', 2, false, 'emotion-positive'),
  jump_joy: _placeholder('jump_joy', 1, false, 'emotion-positive'),
  heart_pose: _placeholder('heart_pose', 1.8, false, 'emotion-positive'),
  // ── §3.8 negative emotion (SHOULD 8) ───────────────────────────
  pout: _placeholder('pout', 1.5, false, 'emotion-negative'),
  stomp_angry: _placeholder('stomp_angry', 1.5, false, 'emotion-negative'),
  sulk: _placeholder('sulk', 3.5, true, 'emotion-negative'),
  cry: _placeholder('cry', 2.5, false, 'emotion-negative'),
  tremble_fear: _placeholder('tremble_fear', 2.5, true, 'emotion-negative'),
  collapse_sad: _placeholder('collapse_sad', 2, false, 'emotion-negative'),
  shake_frustration: _placeholder('shake_frustration', 1.5, false, 'emotion-negative'),
  withdrawal: _placeholder('withdrawal', 3.5, true, 'emotion-negative'),
  // ── §3.9 social (SHOULD 6) ─────────────────────────────────────
  wave_warm: _placeholder('wave_warm', 1.8, false, 'social'),
  bow: {
    name: 'bow',
    duration: 1.5,
    loop: false,
    category: 'social',
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
    tracks: {
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.2, 0, 0.2, 0), kf(0.5, 0, -0.2, 0), kf(0.8, 0, 0.1, 0), kf(1, 0, 0, 0)]
    }
  },
  // ── §3.10 intimate (SHOULD 10) ─────────────────────────────────
  hug_offer: _placeholder('hug_offer', 2, false, 'intimate'),
  hug_receive: _placeholder('hug_receive', 2, false, 'intimate'),
  kiss_lips: _placeholder('kiss_lips', 1.5, false, 'intimate'),
  kiss_cheek: _placeholder('kiss_cheek', 1.5, false, 'intimate'),
  lap_pillow: _placeholder('lap_pillow', 3.5, true, 'intimate'),
  lean_on_shoulder: _placeholder('lean_on_shoulder', 3.5, true, 'intimate'),
  whisper: _placeholder('whisper', 1.5, false, 'intimate'),
  cuddle: _placeholder('cuddle', 3.5, true, 'intimate'),
  hold_hand: _placeholder('hold_hand', 2.5, true, 'intimate'),
  pat_receive: _placeholder('pat_receive', 1.5, false, 'intimate'),
  // ── §3.11 private (SHOULD 5) ───────────────────────────────────
  intimate_embrace: _placeholder('intimate_embrace', 3.5, true, 'private'),
  sleep_together: _placeholder('sleep_together', 5, true, 'private'),
  carry_princess: _placeholder('carry_princess', 3.5, true, 'private'),
  forehead_touch: _placeholder('forehead_touch', 2, false, 'private'),
  nuzzle: _placeholder('nuzzle', 2, false, 'private'),
  // ── §3.12 daily (SHOULD 6) ─────────────────────────────────────
  sit: _placeholder('sit', 3.5, true, 'daily'),
  eat: _placeholder('eat', 2.5, true, 'daily'),
  drink: _placeholder('drink', 2.5, true, 'daily'),
  read: _placeholder('read', 3.5, true, 'daily'),
  pet_animal: _placeholder('pet_animal', 2.5, true, 'daily'),
  exercise_stretch: _placeholder('exercise_stretch', 3.5, true, 'daily'),
  // ── §3.13 surprise (SHOULD 7) ──────────────────────────────────
  surprise_jump: _placeholder('surprise_jump', 1, false, 'surprise'),
  shock_stepback: _placeholder('shock_stepback', 1, false, 'surprise'),
  dizzy: _placeholder('dizzy', 2.5, true, 'surprise'),
  embarrassed_cover: _placeholder('embarrassed_cover', 1.5, false, 'surprise'),
  proud_pose: _placeholder('proud_pose', 1.5, false, 'surprise'),
  relieved_sigh: _placeholder('relieved_sigh', 1.5, false, 'surprise'),
  curious_lean: _placeholder('curious_lean', 1.5, false, 'surprise'),
  // ── §3.15 comfort / healing (SHOULD 6) ─────────────────────────
  comfort_pat: _placeholder('comfort_pat', 1.5, false, 'comfort'),
  pat_head_give: _placeholder('pat_head_give', 2, false, 'comfort'),
  wipe_tears: _placeholder('wipe_tears', 2, false, 'comfort'),
  warm_smile: _placeholder('warm_smile', 1.5, false, 'comfort'),
  reassure_nod: _placeholder('reassure_nod', 1.5, false, 'comfort'),
  hug_comfort: _placeholder('hug_comfort', 2, false, 'comfort'),
  // ── §3.16 weather / environment (SHOULD 5) ─────────────────────
  shiver_cold: _placeholder('shiver_cold', 2.5, true, 'weather'),
  fan_self: _placeholder('fan_self', 2, false, 'weather'),
  sneeze: _placeholder('sneeze', 0.8, false, 'weather'),
  rain_look: _placeholder('rain_look', 1.5, false, 'weather'),
  sunbathe: _placeholder('sunbathe', 4, true, 'weather'),
  // ── §3.17 negative emotion extension (SHOULD 5) ───────────────
  glare: _placeholder('glare', 1.5, false, 'neg-ext'),
  silent_treatment: _placeholder('silent_treatment', 3, true, 'neg-ext'),
  disappointed_walk: _placeholder('disappointed_walk', 1.5, false, 'neg-ext'),
  jealous_pout: _placeholder('jealous_pout', 1.5, false, 'neg-ext'),
  envy_sigh: _placeholder('envy_sigh', 1.5, false, 'neg-ext'),
  // ── §3.18 intimate extension (SHOULD 5) ────────────────────────
  forehead_kiss: _placeholder('forehead_kiss', 1.5, false, 'intim-ext'),
  nose_boop: _placeholder('nose_boop', 1, false, 'intim-ext'),
  hand_kiss: _placeholder('hand_kiss', 1.5, false, 'intim-ext'),
  spoon: _placeholder('spoon', 4, true, 'intim-ext'),
  piggyback: _placeholder('piggyback', 3, true, 'intim-ext'),
  // ── §3.19 music / dance extension (SHOULD 3) ──────────────────
  dance_sway: _placeholder('dance_sway', 3, true, 'music'),
  dance_spin: _placeholder('dance_spin', 2, false, 'music'),
  conduct_music: _placeholder('conduct_music', 3, true, 'music')
}
