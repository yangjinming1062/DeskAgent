import type { ClipDef, Keyframe } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface AquaticBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  topFin: readonly string[]
  bottomFin: readonly string[]
  sideFin: readonly string[]
  tail: readonly string[]
}

export const TRIPO_AQUATIC_BONES: AquaticBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Spine2', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  topFin: ['TopFin'],
  bottomFin: ['BottomFin'],
  sideFin: ['LeftFin', 'RightFin'],
  tail: ['Tail1', 'Tail2', 'Tail3', 'Tail4']
}

function kf(t: number, x: number, y: number, z: number): Keyframe {
  return { t, r: [x, y, z] as const }
}

const S0 = TRIPO_AQUATIC_BONES.spine[0]
const S1 = TRIPO_AQUATIC_BONES.spine[1]
const HD = TRIPO_AQUATIC_BONES.head
const JW = TRIPO_AQUATIC_BONES.jaw
const LF = TRIPO_AQUATIC_BONES.sideFin[0]
const RF = TRIPO_AQUATIC_BONES.sideFin[1]
const TF = TRIPO_AQUATIC_BONES.topFin[0]
const TL0 = TRIPO_AQUATIC_BONES.tail[0]
const TL1 = TRIPO_AQUATIC_BONES.tail[1]

function _aqClip(
  name: string,
  duration: number,
  loop: boolean,
  category: ClipDef['category'],
  tags: readonly string[],
  tracks: Record<string, Keyframe[]>
): ClipDef {
  return { name, duration, loop, category, tags, tracks }
}

export const AQUATIC_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(S0, HD),

  // ── States & Locomotion ──
  aquatic_idle_swim: _aqClip('aquatic_idle_swim', 3.0, true, 'state', ['悠游', '静谧', '从容不迫'], {
    [S0]: [kf(0, 0, 0.15, 0), kf(1.5, 0, -0.15, 0), kf(3.0, 0, 0.15, 0)],
    [TL0]: [kf(0, 0, -0.25, 0), kf(1.5, 0, 0.25, 0), kf(3.0, 0, -0.25, 0)],
    [LF]: [kf(0, 0, 0, 0.2), kf(1.5, 0, 0, -0.1), kf(3.0, 0, 0, 0.2)],
    [RF]: [kf(0, 0, 0, -0.2), kf(1.5, 0, 0, 0.1), kf(3.0, 0, 0, -0.2)]
  }),
  aquatic_sleep_drift: _aqClip('aquatic_sleep_drift', 6.0, true, 'state', ['静谧', '空灵', '深邃'], {
    [S0]: [kf(0, 0.05, 0.05, 0), kf(3.0, 0.02, -0.05, 0), kf(6.0, 0.05, 0.05, 0)],
    [HD]: [kf(0, 0.08, 0, 0), kf(3.0, 0.05, 0, 0), kf(6.0, 0.08, 0, 0)]
  }),
  aquatic_swim_slow: _aqClip('aquatic_swim_slow', 2.0, true, 'locomotion', ['悠游', '游弋', '如鱼得水'], {
    [S0]: [kf(0, 0, 0.25, 0), kf(1.0, 0, -0.25, 0), kf(2.0, 0, 0.25, 0)],
    [TL0]: [kf(0, 0, -0.4, 0), kf(1.0, 0, 0.4, 0), kf(2.0, 0, -0.4, 0)],
    [TL1]: [kf(0, 0, -0.5, 0), kf(1.0, 0, 0.5, 0), kf(2.0, 0, -0.5, 0)],
    [LF]: [kf(0, 0, 0, 0.3), kf(1.0, 0, 0, -0.2), kf(2.0, 0, 0, 0.3)],
    [RF]: [kf(0, 0, 0, -0.3), kf(1.0, 0, 0, 0.2), kf(2.0, 0, 0, -0.3)]
  }),
  aquatic_swim_fast: _aqClip('aquatic_swim_fast', 0.8, true, 'locomotion', ['跃动', '迅捷', '敏捷'], {
    [S0]: [kf(0, 0, 0.4, 0), kf(0.4, 0, -0.4, 0), kf(0.8, 0, 0.4, 0)],
    [TL0]: [kf(0, 0, -0.6, 0), kf(0.4, 0, 0.6, 0), kf(0.8, 0, -0.6, 0)],
    [TL1]: [kf(0, 0, -0.7, 0), kf(0.4, 0, 0.7, 0), kf(0.8, 0, -0.7, 0)]
  }),
  aquatic_dive: _aqClip('aquatic_dive', 1.8, false, 'locomotion', ['深邃', '深海潜行', '冷酷'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.9, 0.6, 0, 0), kf(1.8, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.9, 0.4, 0, 0), kf(1.8, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(0.9, -0.5, 0, 0), kf(1.8, 0, 0, 0)]
  }),
  aquatic_surface: _aqClip('aquatic_surface', 1.5, false, 'locomotion', ['跃动', '灵波荡漾', '轻盈'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.75, -0.5, 0, 0), kf(1.5, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.75, -0.3, 0, 0), kf(1.5, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(0.75, 0.4, 0, 0), kf(1.5, 0, 0, 0)]
  }),
  aquatic_breach: _aqClip('aquatic_breach', 2.0, false, 'locomotion', ['跃动', '欢腾', '华丽'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.8, -0.6, 0, 0), kf(1.4, 0.4, 0, 0), kf(2.0, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(0.8, 0.5, 0.3, 0), kf(2.0, 0, 0, 0)]
  }),

  // ── Interaction & Intimate ──
  aquatic_blow_bubbles: _aqClip('aquatic_blow_bubbles', 2.0, true, 'interaction', ['吐泡', '呆萌', '可爱', '俏皮'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.5, -0.1, 0, 0), kf(1.0, 0.1, 0, 0), kf(2.0, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.5, 0.3, 0, 0), kf(1.0, 0, 0, 0), kf(1.5, 0.3, 0, 0), kf(2.0, 0, 0, 0)]
  }),
  aquatic_gentle_nudge: _aqClip('aquatic_gentle_nudge', 1.5, false, 'intimate', ['温柔', '亲人', '撒娇'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.6, 0.1, 0.25, 0.1), kf(1.5, 0, 0, 0)],
    [LF]: [kf(0, 0, 0, 0), kf(0.6, 0, 0, 0.3), kf(1.5, 0, 0, 0)]
  }),
  aquatic_follow: _aqClip('aquatic_follow', 3.0, true, 'interaction', ['粘人', '忠诚', '亲人'], {
    [S0]: [kf(0, 0, 0.1, 0), kf(1.5, 0, -0.1, 0), kf(3.0, 0, 0.1, 0)],
    [TL0]: [kf(0, 0, 0.2, 0), kf(1.5, 0, -0.2, 0), kf(3.0, 0, 0.2, 0)]
  }),
  aquatic_kiss_fish: _aqClip('aquatic_kiss_fish', 1.2, false, 'intimate', ['俏皮', '可爱', '温婉'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.6, -0.1, 0, 0), kf(1.2, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.6, 0.35, 0, 0), kf(1.2, 0, 0, 0)]
  }),

  // ── Positive & Social ──
  aquatic_happy_spin: _aqClip('aquatic_happy_spin', 2.0, true, 'emotion-positive', ['欢腾', '灵动', '幻彩'], {
    [S0]: [kf(0, 0, 0, 0), kf(1.0, 0, 0, 3.14), kf(2.0, 0, 0, 6.28)],
    [TL0]: [kf(0, 0, 0.3, 0), kf(1.0, 0, -0.3, 0), kf(2.0, 0, 0.3, 0)]
  }),
  aquatic_tail_splash: _aqClip('aquatic_tail_splash', 1.0, true, 'emotion-positive', ['摆尾', '元气', '贪玩'], {
    [TL0]: [kf(0, 0, 0.5, 0), kf(0.5, 0, -0.5, 0), kf(1.0, 0, 0.5, 0)],
    [TL1]: [kf(0, 0, 0.7, 0), kf(0.5, 0, -0.7, 0), kf(1.0, 0, 0.7, 0)]
  }),
  aquatic_school: _aqClip('aquatic_school', 3.0, true, 'social', ['群居', '洄游', '秩序'], {
    [S0]: [kf(0, 0, 0.15, 0), kf(1.5, 0, -0.15, 0), kf(3.0, 0, 0.15, 0)],
    [TL0]: [kf(0, 0, -0.2, 0), kf(1.5, 0, 0.2, 0), kf(3.0, 0, -0.2, 0)]
  }),

  // ── Negative & Defense ──
  aquatic_scared_scatter: _aqClip('aquatic_scared_scatter', 1.0, true, 'emotion-negative', ['胆小', '敏锐', '警惕'], {
    [S0]: [kf(0, 0, 0.5, 0), kf(0.5, 0, -0.5, 0), kf(1.0, 0, 0.5, 0)],
    [TL0]: [kf(0, 0, -0.8, 0), kf(0.5, 0, 0.8, 0), kf(1.0, 0, -0.8, 0)]
  }),
  aquatic_defensive_puff: _aqClip('aquatic_defensive_puff', 2.0, true, 'emotion-negative', ['警戒', '呆萌', '强势'], {
    [S0]: [kf(0, 0.05, 0, 0), kf(1.0, 0.08, 0, 0), kf(2.0, 0.05, 0, 0)],
    [LF]: [kf(0, 0, 0, 0.6), kf(1.0, 0, 0, 0.8), kf(2.0, 0, 0, 0.6)],
    [RF]: [kf(0, 0, 0, -0.6), kf(1.0, 0, 0, -0.8), kf(2.0, 0, 0, -0.6)]
  })
}
