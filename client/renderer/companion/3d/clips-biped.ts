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

export function buildClip(def: ClipDef, restQuats?: ReadonlyMap<string, THREE.Quaternion>): THREE.AnimationClip {
  const tracks: THREE.QuaternionKeyframeTrack[] = []

  for (const [bone, kfs] of Object.entries(def.tracks)) {
    if (kfs.length < 2) {
      continue
    }

    const times: number[] = []
    const values: number[] = []
    const restQ = restQuats?.get(bone)

    for (const kf of kfs) {
      times.push(kf.t)
      _EULER.set(kf.r[0], kf.r[1], kf.r[2], 'XYZ')
      _QUAT.setFromEuler(_EULER)

      let qx = _QUAT.x
      let qy = _QUAT.y
      let qz = _QUAT.z
      let qw = _QUAT.w

      if (restQ) {
        const finalQ = _QUAT.clone().multiply(restQ)
        qx = finalQ.x
        qy = finalQ.y
        qz = finalQ.z
        qw = finalQ.w
      }

      values.push(qx, qy, qz, qw)
    }

    tracks.push(new THREE.QuaternionKeyframeTrack(`${bone}.quaternion`, times, values))
  }

  return new THREE.AnimationClip(def.name, def.duration, tracks)
}

export function kf(t: number, x: number, y: number, z: number): Keyframe {
  return { t, r: [x, y, z] as const }
}

// 各骨骼类型动画库共用的 clip 工厂。它们都在重复 6 字段的对象字面量；
// 一份统一定义让它们保持同步。
export function makeClip(
  name: string,
  duration: number,
  loop: boolean,
  category: ClipDef['category'],
  tags: readonly string[],
  tracks: Record<string, Keyframe[]>
): ClipDef {
  return { name, duration, loop, category, tags, tracks }
}

/** §3.1 的非人形骨骼标准状态 clip（AnimationMap 按这些精确名字解析状态，
 * 因此每个骨骼库都必须提供它们）。在 per-rig 关键帧就绪之前，
 * 用脊柱 + 头部的占位动作临时填充。 */
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

// 闲置微动作与场景 idle 用 —— 轻微脊柱/手臂活动，比 idle 更省关键帧
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
      ],
      LeftArm: [kf(0, 0.06, 0.04, -0.3), kf(duration / 2, 0.07, 0.05, -0.34), kf(duration, 0.06, 0.04, -0.3)],
      LeftForeArm: [kf(0, 0.18, 0, -0.04), kf(duration / 2, 0.2, 0, -0.06), kf(duration, 0.18, 0, -0.04)],
      RightArm: [kf(0, 0.06, -0.04, 0.3), kf(duration / 2, 0.07, -0.05, 0.34), kf(duration, 0.06, -0.04, 0.3)],
      RightForeArm: [kf(0, 0.18, 0, 0.04), kf(duration / 2, 0.2, 0, 0.06), kf(duration, 0.18, 0, 0.04)]
    }
  }
}

const _HIPS = 'Hips'
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
  // ── §3.1 核心状态（MUST 9）─────────────────────────────────
  idle: {
    name: 'idle',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(2, 0.02, 0, 0.005), kf(4, 0, 0, 0)],
      [_SPINE1]: [kf(0, 0, 0, 0), kf(2, 0.025, 0, -0.005), kf(4, 0, 0, 0)],
      [_NECK]: [kf(0, -0.01, 0, 0), kf(2, 0.01, 0, 0), kf(4, -0.01, 0, 0)],
      [_HEAD]: [
        kf(0, -0.02, 0.015, 0),
        kf(1, -0.01, 0.025, 0.005),
        kf(2, -0.025, 0.01, 0),
        kf(3, -0.015, -0.01, -0.005),
        kf(4, -0.02, 0.015, 0)
      ],
      [_LEFT_ARM]: [kf(0, -0.12, 0.04, -0.22), kf(2, -0.1, 0.05, -0.25), kf(4, -0.12, 0.04, -0.22)],
      [_LEFT_FORE]: [kf(0, 0.18, 0, -0.04), kf(2, 0.22, 0, -0.06), kf(4, 0.18, 0, -0.04)],
      [_RIGHT_ARM]: [kf(0, 0.12, -0.04, 0.22), kf(2, 0.1, -0.05, 0.25), kf(4, 0.12, -0.04, 0.22)],
      [_RIGHT_FORE]: [kf(0, 0.18, 0, 0.04), kf(2, 0.22, 0, 0.06), kf(4, 0.18, 0, 0.04)],
      [_LEFT_UP]: [kf(0, 0.01, 0, -0.01), kf(2, -0.01, 0, 0.01), kf(4, 0.01, 0, -0.01)],
      [_RIGHT_UP]: [kf(0, -0.01, 0, 0.01), kf(2, 0.01, 0, -0.01), kf(4, -0.01, 0, 0.01)]
    }
  },
  listening: {
    name: 'listening',
    duration: 3.5,
    loop: true,
    category: 'state',
    tracks: {
      [_HEAD]: [kf(0, 0.04, 0.08, 0), kf(1.75, 0.03, 0.1, 0), kf(3.5, 0.04, 0.08, 0)],
      [_SPINE]: [kf(0, -0.02, 0, 0), kf(1.75, -0.01, 0, 0), kf(3.5, -0.02, 0, 0)],
      [_NECK]: [kf(0, -0.03, 0, 0), kf(1.75, -0.02, 0, 0), kf(3.5, -0.03, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.06, 0.04, -0.3), kf(1.75, 0.08, 0.06, -0.34), kf(3.5, 0.06, 0.04, -0.3)],
      [_LEFT_FORE]: [kf(0, 0.2, 0, -0.06), kf(1.75, 0.25, 0, -0.08), kf(3.5, 0.2, 0, -0.06)],
      [_RIGHT_ARM]: [kf(0, 0.06, -0.04, 0.3), kf(1.75, 0.08, -0.06, 0.34), kf(3.5, 0.06, -0.04, 0.3)],
      [_RIGHT_FORE]: [kf(0, 0.2, 0, 0.06), kf(1.75, 0.25, 0, 0.08), kf(3.5, 0.2, 0, 0.06)]
    }
  },
  thinking: {
    name: 'thinking',
    duration: 4,
    loop: true,
    category: 'state',
    tracks: {
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, 0.3), kf(2, 0.08, 0.03, 0.34), kf(4, 0.05, 0.05, 0.3)],
      [_LEFT_FORE]: [kf(0, 0.15, 0, 0.08), kf(2, 0.2, 0, 0.1), kf(4, 0.15, 0, 0.08)],
      [_RIGHT_ARM]: [kf(0, -0.3, 0, 0.1), kf(2, -0.25, 0, 0.12), kf(4, -0.3, 0, 0.1)],
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
        kf(0, 0.1, 0.2, -0.55),
        kf(1, 0.25, 0.35, -0.65),
        kf(2, 0.12, 0.15, -0.5),
        kf(3, 0.2, 0.3, -0.6),
        kf(4, 0.1, 0.2, -0.55)
      ],
      [_LEFT_FORE]: [
        kf(0, 0.3, 0, 0.15),
        kf(1, 0.5, 0, 0.22),
        kf(2, 0.35, 0, 0.15),
        kf(3, 0.45, 0, 0.2),
        kf(4, 0.3, 0, 0.15)
      ],
      [_RIGHT_ARM]: [
        kf(0, 0.1, -0.2, 0.55),
        kf(1.5, 0.25, -0.35, 0.65),
        kf(3, 0.15, -0.18, 0.5),
        kf(4, 0.1, -0.2, 0.55)
      ],
      [_RIGHT_FORE]: [kf(0, 0.3, 0, -0.15), kf(1.5, 0.5, 0, -0.22), kf(3, 0.35, 0, -0.15), kf(4, 0.3, 0, -0.15)],
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
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, -0.3), kf(3, 0.06, 0.04, -0.34), kf(6, 0.05, 0.05, -0.3)],
      [_LEFT_FORE]: [kf(0, 0.2, 0, -0.06), kf(3, 0.22, 0, -0.06), kf(6, 0.2, 0, -0.06)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, 0.3), kf(3, 0.06, -0.04, 0.34), kf(6, 0.05, -0.05, 0.3)],
      [_RIGHT_FORE]: [kf(0, 0.2, 0, 0.06), kf(3, 0.22, 0, 0.06), kf(6, 0.2, 0, 0.06)]
    }
  },
  interacting: {
    name: 'interacting',
    duration: 1.5,
    loop: false,
    category: 'state',
    tracks: {
      [_SPINE]: [kf(0, 0, 0, 0), kf(0.3, 0.02, 0, 0), kf(1.5, 0, 0, 0)],
      [_HEAD]: [kf(0, 0, 0, 0), kf(0.3, 0.04, 0, 0), kf(1.5, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.06, 0.04, -0.3), kf(0.3, 0.15, 0.08, -0.22), kf(1.5, 0.06, 0.04, -0.3)],
      [_LEFT_FORE]: [kf(0, 0.18, 0, -0.04), kf(0.3, 0.35, 0, -0.08), kf(1.5, 0.18, 0, -0.04)],
      [_RIGHT_ARM]: [kf(0, 0.06, -0.04, 0.3), kf(0.3, 0.15, -0.08, 0.22), kf(1.5, 0.06, -0.04, 0.3)],
      [_RIGHT_FORE]: [kf(0, 0.18, 0, 0.04), kf(0.3, 0.35, 0, 0.08), kf(1.5, 0.18, 0, 0.04)]
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
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, -0.28), kf(1.75, 0.08, 0.03, -0.32), kf(3.5, 0.05, 0.05, -0.28)],
      [_LEFT_FORE]: [kf(0, 0.18, 0, -0.04), kf(1.75, 0.2, 0, -0.06), kf(3.5, 0.18, 0, -0.04)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, 0.28), kf(1.75, 0.08, -0.03, 0.32), kf(3.5, 0.05, -0.05, 0.28)],
      [_RIGHT_FORE]: [kf(0, 0.18, 0, 0.04), kf(1.75, 0.2, 0, 0.06), kf(3.5, 0.18, 0, 0.04)]
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
      [_LEFT_ARM]: [kf(0, 0.05, 0.05, -0.3), kf(2.5, 0.06, 0.04, -0.34), kf(5, 0.05, 0.05, -0.3)],
      [_LEFT_FORE]: [kf(0, 0.18, 0, -0.04), kf(2.5, 0.2, 0, -0.06), kf(5, 0.18, 0, -0.04)],
      [_RIGHT_ARM]: [kf(0, 0.05, -0.05, 0.3), kf(2.5, 0.06, -0.04, 0.34), kf(5, 0.05, -0.05, 0.3)],
      [_RIGHT_FORE]: [kf(0, 0.18, 0, 0.04), kf(2.5, 0.2, 0, 0.06), kf(5, 0.18, 0, 0.04)]
    }
  },
  // ── §3.2 / §3.3 闲置微动作（4 手写 + 8 scene-idle 占位） ──
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
  // ── §3.4 移动（locomotion: walk / jump / fly / drag） ──
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
  // 蹲下蓄力 → 起跳 → 滞空 → 落地回正
  jump: {
    name: 'jump',
    duration: 1.0,
    loop: false,
    category: 'locomotion',
    tags: ['元气', '好动', '活泼'],
    tracks: {
      [_HIPS]: [kf(0, 0, 0, 0), kf(0.3, 0, 0, 0), kf(0.5, 0, 0, 0.4), kf(0.7, 0, 0, -0.2), kf(1.0, 0, 0, 0)],
      [_LEFT_UP]: [
        kf(0, 0.01, 0, -0.01),
        kf(0.3, -0.5, 0, 0),
        kf(0.5, 0.6, 0, 0),
        kf(0.7, 0.05, 0, 0),
        kf(1.0, 0.01, 0, -0.01)
      ],
      [_RIGHT_UP]: [
        kf(0, -0.01, 0, 0.01),
        kf(0.3, -0.5, 0, 0),
        kf(0.5, 0.6, 0, 0),
        kf(0.7, 0.05, 0, 0),
        kf(1.0, -0.01, 0, 0.01)
      ],
      [_LEFT_LEG]: [
        kf(0, -0.1, 0, 0),
        kf(0.3, 0.4, 0, 0),
        kf(0.5, -0.2, 0, 0),
        kf(0.7, 0.1, 0, 0),
        kf(1.0, -0.1, 0, 0)
      ],
      [_RIGHT_LEG]: [
        kf(0, 0.15, 0, 0),
        kf(0.3, 0.4, 0, 0),
        kf(0.5, -0.2, 0, 0),
        kf(0.7, 0.1, 0, 0),
        kf(1.0, 0.15, 0, 0)
      ],
      [_LEFT_ARM]: [
        kf(0, -0.12, 0.04, -1.1),
        kf(0.3, -0.3, 0.2, -0.6),
        kf(0.5, -0.5, 0.4, -0.4),
        kf(0.7, -0.4, 0.3, -0.3),
        kf(1.0, -0.12, 0.04, -1.1)
      ],
      [_RIGHT_ARM]: [
        kf(0, 0.12, -0.04, 1.1),
        kf(0.3, -0.3, -0.2, 0.6),
        kf(0.5, -0.5, -0.4, 0.4),
        kf(0.7, -0.4, -0.3, 0.3),
        kf(1.0, 0.12, -0.04, 1.1)
      ],
      [_SPINE]: [kf(0, -0.04, 0, 0), kf(0.3, 0.15, 0, 0), kf(0.5, -0.1, 0, 0), kf(1.0, -0.04, 0, 0)]
    }
  },
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
      [_HEAD]: [kf(0, -0.12, 0.03, 0), kf(0.8, -0.16, -0.03, 0), kf(1.6, -0.12, 0.03, 0)],
      [_SPINE]: [kf(0, 0.04, 0, 0.01), kf(0.8, 0.03, 0, -0.01), kf(1.6, 0.04, 0, 0.01)],
      [_LEFT_ARM]: [kf(0, 0.1, 0.1, 0.92), kf(0.8, 0.15, 0.05, 0.85), kf(1.6, 0.1, 0.1, 0.92)],
      [_LEFT_FORE]: [kf(0, 0.25, 0, 0.08), kf(0.8, 0.35, 0, 0.1), kf(1.6, 0.25, 0, 0.08)],
      [_RIGHT_ARM]: [kf(0, 0.1, -0.1, -0.92), kf(0.8, 0.15, -0.05, -0.85), kf(1.6, 0.1, -0.1, -0.92)],
      [_RIGHT_FORE]: [kf(0, 0.25, 0, -0.08), kf(0.8, 0.35, 0, -0.1), kf(1.6, 0.25, 0, -0.08)],
      [_LEFT_UP]: [kf(0, -0.06, 0, -0.02), kf(0.8, 0.06, 0, -0.02), kf(1.6, -0.06, 0, -0.02)],
      [_LEFT_LEG]: [kf(0, 0.08, 0, 0), kf(0.8, 0.02, 0, 0), kf(1.6, 0.08, 0, 0)],
      [_RIGHT_UP]: [kf(0, 0.06, 0, 0.02), kf(0.8, -0.06, 0, 0.02), kf(1.6, 0.06, 0, 0.02)],
      [_RIGHT_LEG]: [kf(0, 0.02, 0, 0), kf(0.8, 0.08, 0, 0), kf(1.6, 0.02, 0, 0)]
    }
  },
  // ── §3.5 互动反应（interaction: poke_light / poke_heavy / poke_happy / drag_end） ──
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
  drag_end: {
    name: 'drag_end',
    duration: 0.5,
    loop: false,
    category: 'interaction',
    tags: ['温柔', '体贴', '活泼'],
    tracks: {
      [_SPINE]: [kf(0, 0.04, 0, 0), kf(0.2, -0.05, 0, 0), kf(0.5, 0, 0, 0)],
      [_HEAD]: [kf(0, -0.1, 0, 0), kf(0.2, 0.05, 0, 0), kf(0.5, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.1, 0, -0.95), kf(0.25, 0.08, 0, -1.15), kf(0.5, 0.05, 0.05, -1.25)],
      [_LEFT_FORE]: [kf(0, 0.25, 0, -0.08), kf(0.25, 0.18, 0, -0.1), kf(0.5, 0.15, 0, -0.1)],
      [_RIGHT_ARM]: [kf(0, 0.1, 0, 0.95), kf(0.25, 0.08, 0, 1.15), kf(0.5, 0.05, -0.05, 1.25)],
      [_RIGHT_FORE]: [kf(0, 0.25, 0, 0.08), kf(0.25, 0.18, 0, 0.1), kf(0.5, 0.15, 0, 0.1)],
      [_LEFT_UP]: [kf(0, 0, 0, 0), kf(0.2, -0.04, 0, 0), kf(0.5, 0, 0, 0)],
      [_LEFT_LEG]: [kf(0, 0.05, 0, 0), kf(0.2, 0.08, 0, 0), kf(0.5, 0, 0, 0)],
      [_RIGHT_UP]: [kf(0, 0, 0, 0), kf(0.2, -0.04, 0, 0), kf(0.5, 0, 0, 0)],
      [_RIGHT_LEG]: [kf(0, 0.05, 0, 0), kf(0.2, 0.08, 0, 0), kf(0.5, 0, 0, 0)]
    }
  },
  // ── §3.6 仪式动作（ritual: greeting / goodbye） ──
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
  // ── §3.7 正面情绪（clap） ──
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
  // ── §3.9 社交（5 个：bow / fold_arms / standing_relax / shrug / shake_head） ──
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
      [_HEAD]: [kf(0, 0, 0, 0), kf(1.25, 0.01, 0.02, 0), kf(2.5, 0, 0, 0)],
      [_LEFT_ARM]: [kf(0, 0.06, 0.04, -1.25), kf(1.25, 0.07, 0.05, -1.27), kf(2.5, 0.06, 0.04, -1.25)],
      [_LEFT_FORE]: [kf(0, 0.18, 0, -0.06), kf(1.25, 0.2, 0, -0.08), kf(2.5, 0.18, 0, -0.06)],
      [_RIGHT_ARM]: [kf(0, 0.06, -0.04, 1.25), kf(1.25, 0.07, -0.05, 1.27), kf(2.5, 0.06, -0.04, 1.25)],
      [_RIGHT_FORE]: [kf(0, 0.18, 0, 0.06), kf(1.25, 0.2, 0, 0.08), kf(2.5, 0.18, 0, 0.06)]
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
  }
}
