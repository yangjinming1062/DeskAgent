import type { ClipDef, Keyframe } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface AvianBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  leftWing: readonly string[]
  rightWing: readonly string[]
  leftLeg: readonly string[]
  rightLeg: readonly string[]
  tail: readonly string[]
}

export const TRIPO_AVIAN_BONES: AvianBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  leftWing: ['LeftWing1', 'LeftWing2', 'LeftWing3'],
  rightWing: ['RightWing1', 'RightWing2', 'RightWing3'],
  leftLeg: ['LeftLeg', 'LeftFoot'],
  rightLeg: ['RightLeg', 'RightFoot'],
  tail: ['Tail1', 'Tail2', 'Tail3']
}

function kf(t: number, x: number, y: number, z: number): Keyframe {
  return { t, r: [x, y, z] as const }
}

const S0 = TRIPO_AVIAN_BONES.spine[0]
const S1 = TRIPO_AVIAN_BONES.spine[1]
const HD = TRIPO_AVIAN_BONES.head
const JW = TRIPO_AVIAN_BONES.jaw
const LW0 = TRIPO_AVIAN_BONES.leftWing[0]
const LW1 = TRIPO_AVIAN_BONES.leftWing[1]
const RW0 = TRIPO_AVIAN_BONES.rightWing[0]
const RW1 = TRIPO_AVIAN_BONES.rightWing[1]
const TL = TRIPO_AVIAN_BONES.tail[0]
const LL = TRIPO_AVIAN_BONES.leftLeg[0]
const RL = TRIPO_AVIAN_BONES.rightLeg[0]

function _aClip(
  name: string,
  duration: number,
  loop: boolean,
  category: ClipDef['category'],
  tags: readonly string[],
  tracks: Record<string, Keyframe[]>
): ClipDef {
  return { name, duration, loop, category, tags, tracks }
}

export const AVIAN_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(S0, HD),

  // ── States & Locomotion ──
  avian_idle: _aClip('avian_idle', 4, true, 'state', ['高贵', '从容不迫', '文静'], {
    [S0]: [kf(0, 0, 0, 0), kf(2, 0.02, 0, 0), kf(4, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(2, 0.04, 0.03, 0), kf(4, 0, 0, 0)],
    [TL]: [kf(0, 0, 0, 0), kf(2, 0.02, 0, 0), kf(4, 0, 0, 0)]
  }),
  avian_perch: _aClip('avian_perch', 5, true, 'state', ['孤傲', '从容不迫', '威严'], {
    [S0]: [kf(0, 0.03, 0, 0), kf(2.5, 0.05, 0, 0), kf(5, 0.03, 0, 0)],
    [HD]: [kf(0, 0, 0.1, 0), kf(2.5, 0, -0.1, 0), kf(5, 0, 0.1, 0)],
    [LW0]: [kf(0, 0, 0, 0.1), kf(2.5, 0, 0, 0.12), kf(5, 0, 0, 0.1)],
    [RW0]: [kf(0, 0, 0, -0.1), kf(2.5, 0, 0, -0.12), kf(5, 0, 0, -0.1)]
  }),
  avian_sleep: _aClip('avian_sleep', 6, true, 'state', ['温顺', '文静', '安静'], {
    [HD]: [kf(0, 0.3, 0.6, 0), kf(3, 0.35, 0.65, 0), kf(6, 0.3, 0.6, 0)],
    [S0]: [kf(0, -0.05, 0, 0), kf(3, -0.08, 0, 0), kf(6, -0.05, 0, 0)]
  }),
  avian_listen: _aClip('avian_listen', 3, true, 'state', ['机敏', '警觉锐利', '灵巧'], {
    [HD]: [kf(0, 0.05, 0.2, 0.1), kf(1.5, 0.05, -0.2, -0.1), kf(3, 0.05, 0.2, 0.1)]
  }),
  avian_walk: _aClip('avian_walk', 1.0, true, 'locomotion', ['灵巧', '轻盈', '机敏'], {
    [HD]: [kf(0, 0.1, 0, 0), kf(0.5, -0.08, 0, 0), kf(1.0, 0.1, 0, 0)],
    [LL]: [kf(0, 0.3, 0, 0), kf(0.5, -0.2, 0, 0), kf(1.0, 0.3, 0, 0)],
    [RL]: [kf(0, -0.2, 0, 0), kf(0.5, 0.3, 0, 0), kf(1.0, -0.2, 0, 0)],
    [TL]: [kf(0, 0, 0.05, 0), kf(0.5, 0, -0.05, 0), kf(1.0, 0, 0.05, 0)]
  }),
  avian_hop: _aClip('avian_hop', 0.6, true, 'locomotion', ['活泼', '轻盈', '元气'], {
    [S0]: [kf(0, 0.1, 0, 0), kf(0.3, -0.2, 0, 0), kf(0.6, 0.1, 0, 0)],
    [LL]: [kf(0, 0.2, 0, 0), kf(0.3, -0.3, 0, 0), kf(0.6, 0.2, 0, 0)],
    [RL]: [kf(0, 0.2, 0, 0), kf(0.3, -0.3, 0, 0), kf(0.6, 0.2, 0, 0)],
    [TL]: [kf(0, 0.2, 0, 0), kf(0.3, -0.1, 0, 0), kf(0.6, 0.2, 0, 0)]
  }),
  avian_fly_glide: _aClip('avian_fly_glide', 3.0, true, 'locomotion', ['翱翔', '优雅', '羽翼丰满', '从容不迫'], {
    [LW0]: [kf(0, 0, 0, 0.8), kf(1.5, 0, 0, 0.85), kf(3.0, 0, 0, 0.8)],
    [RW0]: [kf(0, 0, 0, -0.8), kf(1.5, 0, 0, -0.85), kf(3.0, 0, 0, -0.8)],
    [TL]: [kf(0, -0.1, 0, 0), kf(1.5, -0.05, 0, 0), kf(3.0, -0.1, 0, 0)],
    [S0]: [kf(0, 0.05, 0, 0), kf(1.5, 0.02, 0, 0), kf(3.0, 0.05, 0, 0)]
  }),
  avian_fly_flap: _aClip('avian_fly_flap', 0.8, true, 'locomotion', ['翱翔', '展翅', '轻盈', '精力充沛'], {
    [LW0]: [kf(0, 0, 0, 1.1), kf(0.4, 0, 0, -0.5), kf(0.8, 0, 0, 1.1)],
    [RW0]: [kf(0, 0, 0, -1.1), kf(0.4, 0, 0, 0.5), kf(0.8, 0, 0, -1.1)],
    [S0]: [kf(0, -0.05, 0, 0), kf(0.4, 0.08, 0, 0), kf(0.8, -0.05, 0, 0)]
  }),
  avian_takeoff: _aClip('avian_takeoff', 1.2, false, 'locomotion', ['展翅', '灵巧', '翱翔'], {
    [S0]: [kf(0, 0.2, 0, 0), kf(0.4, -0.3, 0, 0), kf(1.2, 0, 0, 0)],
    [LW0]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, 1.2), kf(0.8, 0, 0, -0.4), kf(1.2, 0, 0, 0.8)],
    [RW0]: [kf(0, 0, 0, 0), kf(0.4, 0, 0, -1.2), kf(0.8, 0, 0, 0.4), kf(1.2, 0, 0, -0.8)],
    [LL]: [kf(0, 0.3, 0, 0), kf(0.4, -0.5, 0, 0), kf(1.2, -0.2, 0, 0)],
    [RL]: [kf(0, 0.3, 0, 0), kf(0.4, -0.5, 0, 0), kf(1.2, -0.2, 0, 0)]
  }),
  avian_land: _aClip('avian_land', 1.0, false, 'locomotion', ['从容不迫', '轻盈', '优雅'], {
    [LW0]: [kf(0, 0, 0, 1.0), kf(0.5, 0, 0, 0.4), kf(1.0, 0, 0, 0)],
    [RW0]: [kf(0, 0, 0, -1.0), kf(0.5, 0, 0, -0.4), kf(1.0, 0, 0, 0)],
    [LL]: [kf(0, -0.4, 0, 0), kf(0.5, 0.3, 0, 0), kf(1.0, 0, 0, 0)],
    [RL]: [kf(0, -0.4, 0, 0), kf(0.5, 0.3, 0, 0), kf(1.0, 0, 0, 0)],
    [TL]: [kf(0, 0.3, 0, 0), kf(0.5, 0.4, 0, 0), kf(1.0, 0, 0, 0)]
  }),
  avian_dive: _aClip('avian_dive', 1.5, false, 'locomotion', ['俯冲', '迅捷', '敏锐'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.75, 0.8, 0, 0), kf(1.5, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.75, -0.6, 0, 0), kf(1.5, 0, 0, 0)],
    [LW0]: [kf(0, 0, 0, 0.8), kf(0.75, 0, 0, 0.1), kf(1.5, 0, 0, 0.8)],
    [RW0]: [kf(0, 0, 0, -0.8), kf(0.75, 0, 0, -0.1), kf(1.5, 0, 0, -0.8)]
  }),

  // ── Daily & Routine ──
  avian_preen: _aClip('avian_preen', 3.0, true, 'daily', ['高贵', '华丽', '优雅', '羽翼丰满'], {
    [HD]: [kf(0, 0.2, 0.4, 0), kf(1.0, 0.35, 0.5, 0.1), kf(2.0, 0.2, -0.4, 0), kf(3.0, 0.2, 0.4, 0)],
    [LW0]: [kf(0, 0, 0, 0.2), kf(1.0, 0, 0, 0.4), kf(2.0, 0, 0, 0.2), kf(3.0, 0, 0, 0.2)]
  }),
  avian_peck: _aClip('avian_peck', 0.8, false, 'daily', ['贪吃', '灵巧', '机敏'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.2, 0.45, 0, 0), kf(0.4, 0, 0, 0), kf(0.6, 0.5, 0, 0), kf(0.8, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.2, 0.2, 0, 0), kf(0.4, 0, 0, 0), kf(0.6, 0.2, 0, 0), kf(0.8, 0, 0, 0)]
  }),
  avian_drink: _aClip('avian_drink', 2.0, false, 'daily', ['温顺', '优雅'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.5, 0.5, 0, 0), kf(1.2, -0.4, 0, 0), kf(2.0, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.5, 0.2, 0, 0), kf(1.2, 0.1, 0, 0), kf(2.0, 0, 0, 0)]
  }),
  avian_stretch_wing: _aClip('avian_stretch_wing', 2.5, false, 'daily', ['展翅', '优雅', '华丽'], {
    [LW0]: [kf(0, 0, 0, 0), kf(1.2, 0, 0, 1.2), kf(2.5, 0, 0, 0)],
    [LL]: [kf(0, 0, 0, 0), kf(1.2, -0.4, 0, 0), kf(2.5, 0, 0, 0)],
    [TL]: [kf(0, 0, 0, 0), kf(1.2, 0.2, 0.2, 0), kf(2.5, 0, 0, 0)]
  }),
  avian_dust_bath: _aClip('avian_dust_bath', 2.5, true, 'daily', ['可爱', '活泼', '憨厚'], {
    [S0]: [kf(0, 0, 0.1, 0), kf(1.25, 0, -0.1, 0), kf(2.5, 0, 0.1, 0)],
    [LW0]: [kf(0, 0, 0, 0.6), kf(0.6, 0, 0, 0.1), kf(1.25, 0, 0, 0.6), kf(1.8, 0, 0, 0.1), kf(2.5, 0, 0, 0.6)],
    [RW0]: [kf(0, 0, 0, -0.6), kf(0.6, 0, 0, -0.1), kf(1.25, 0, 0, -0.6), kf(1.8, 0, 0, -0.1), kf(2.5, 0, 0, -0.6)]
  }),
  avian_build_nest: _aClip('avian_build_nest', 3.5, true, 'daily', ['勤劳', '体贴', '顺从'], {
    [HD]: [kf(0, 0.2, 0, 0), kf(0.8, 0.4, 0.2, 0), kf(1.8, 0.35, -0.2, 0), kf(2.8, 0.45, 0, 0), kf(3.5, 0.2, 0, 0)]
  }),

  // ── Interaction & Ritual ──
  avian_sing: _aClip('avian_sing', 3.5, true, 'interaction', ['啼鸣', '鸣啭', '华丽', '轻快活泼'], {
    [HD]: [
      kf(0, -0.1, 0, 0),
      kf(0.8, -0.3, 0.1, 0),
      kf(1.8, -0.35, -0.1, 0),
      kf(2.8, -0.25, 0, 0),
      kf(3.5, -0.1, 0, 0)
    ],
    [JW]: [kf(0, 0, 0, 0), kf(0.8, 0.3, 0, 0), kf(1.8, 0.35, 0, 0), kf(2.8, 0.25, 0, 0), kf(3.5, 0, 0, 0)],
    [TL]: [kf(0, 0.1, 0.05, 0), kf(1.8, 0.1, -0.05, 0), kf(3.5, 0.1, 0.05, 0)]
  }),
  avian_nuzzle: _aClip('avian_nuzzle', 2.0, false, 'intimate', ['撒娇', '亲人', '温柔', '温婉'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.8, 0.1, 0.3, 0.15), kf(2.0, 0, 0, 0)],
    [LW0]: [kf(0, 0, 0, 0), kf(0.8, 0, 0, 0.2), kf(2.0, 0, 0, 0)]
  }),
  avian_mating_display: _aClip('avian_mating_display', 3.0, true, 'ritual', ['求偶', '华丽', '高贵', '展翅'], {
    [LW0]: [kf(0, 0, 0, 0.8), kf(1.5, 0, 0, 1.2), kf(3.0, 0, 0, 0.8)],
    [RW0]: [kf(0, 0, 0, -0.8), kf(1.5, 0, 0, -1.2), kf(3.0, 0, 0, -0.8)],
    [TL]: [kf(0, 0.4, 0, 0), kf(1.5, 0.6, 0, 0), kf(3.0, 0.4, 0, 0)],
    [HD]: [kf(0, -0.2, 0, 0), kf(1.5, -0.3, 0, 0), kf(3.0, -0.2, 0, 0)]
  }),
  avian_bow: _aClip('avian_bow', 2.0, false, 'ritual', ['高贵', '体面', '优雅'], {
    [S0]: [kf(0, 0, 0, 0), kf(1.0, 0.35, 0, 0), kf(2.0, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(1.0, 0.2, 0, 0), kf(2.0, 0, 0, 0)],
    [LW0]: [kf(0, 0, 0, 0), kf(1.0, 0, 0, 0.4), kf(2.0, 0, 0, 0)],
    [RW0]: [kf(0, 0, 0, 0), kf(1.0, 0, 0, -0.4), kf(2.0, 0, 0, 0)]
  }),
  avian_shoulder_perch: _aClip('avian_shoulder_perch', 4.0, true, 'intimate', ['亲人', '忠诚', '粘人'], {
    [S0]: [kf(0, 0.02, 0, 0), kf(2.0, 0.04, 0, 0), kf(4.0, 0.02, 0, 0)],
    [HD]: [kf(0, 0, 0.1, 0), kf(2.0, 0.05, -0.1, 0), kf(4.0, 0, 0.1, 0)]
  }),

  // ── Positive Emotion ──
  avian_happy_chirp: _aClip('avian_happy_chirp', 1.5, true, 'emotion-positive', ['欢腾', '开朗', '元气', '灵巧'], {
    [HD]: [kf(0, 0.05, 0, 0), kf(0.35, -0.15, 0, 0), kf(0.75, 0.05, 0, 0), kf(1.1, -0.15, 0, 0), kf(1.5, 0.05, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.35, 0.25, 0, 0), kf(0.75, 0, 0, 0), kf(1.1, 0.25, 0, 0), kf(1.5, 0, 0, 0)],
    [TL]: [kf(0, 0.1, 0.1, 0), kf(0.75, 0.1, -0.1, 0), kf(1.5, 0.1, 0.1, 0)]
  }),
  avian_wing_flutter: _aClip('avian_wing_flutter', 1.0, true, 'emotion-positive', ['欢腾', '活泼', '轻盈'], {
    [LW0]: [kf(0, 0, 0, 0.3), kf(0.25, 0, 0, 0.7), kf(0.5, 0, 0, 0.3), kf(0.75, 0, 0, 0.7), kf(1.0, 0, 0, 0.3)],
    [RW0]: [kf(0, 0, 0, -0.3), kf(0.25, 0, 0, -0.7), kf(0.5, 0, 0, -0.3), kf(0.75, 0, 0, -0.7), kf(1.0, 0, 0, -0.3)]
  }),
  avian_head_bob: _aClip('avian_head_bob', 1.2, true, 'emotion-positive', ['俏皮', '呆萌', '搞怪'], {
    [HD]: [kf(0, 0.1, 0, 0), kf(0.3, -0.15, 0, 0), kf(0.6, 0.1, 0, 0), kf(0.9, -0.15, 0, 0), kf(1.2, 0.1, 0, 0)]
  }),
  avian_proud_crest: _aClip('avian_proud_crest', 2.5, true, 'emotion-positive', ['高傲', '高贵', '威严'], {
    [S0]: [kf(0, -0.1, 0, 0), kf(1.25, -0.15, 0, 0), kf(2.5, -0.1, 0, 0)],
    [HD]: [kf(0, -0.15, 0, 0), kf(1.25, -0.2, 0, 0), kf(2.5, -0.15, 0, 0)],
    [TL]: [kf(0, 0.3, 0, 0), kf(1.25, 0.35, 0, 0), kf(2.5, 0.3, 0, 0)]
  }),

  // ── Negative Emotion & Defense ──
  avian_scared_flap: _aClip('avian_scared_flap', 1.2, true, 'emotion-negative', ['胆小', '警惕', '聒噪'], {
    [LW0]: [kf(0, 0, 0, 0.8), kf(0.3, 0, 0, 0.2), kf(0.6, 0, 0, 0.8), kf(0.9, 0, 0, 0.2), kf(1.2, 0, 0, 0.8)],
    [RW0]: [kf(0, 0, 0, -0.8), kf(0.3, 0, 0, -0.2), kf(0.6, 0, 0, -0.8), kf(0.9, 0, 0, -0.2), kf(1.2, 0, 0, -0.8)],
    [HD]: [kf(0, 0.2, 0, 0), kf(0.6, 0.25, 0, 0), kf(1.2, 0.2, 0, 0)]
  }),
  avian_threat_hiss: _aClip('avian_threat_hiss', 2.0, true, 'emotion-negative', ['凶猛', '警戒', '聒噪', '暴躁'], {
    [S0]: [kf(0, 0.2, 0, 0), kf(1.0, 0.25, 0, 0), kf(2.0, 0.2, 0, 0)],
    [HD]: [kf(0, 0.3, 0, 0), kf(1.0, 0.35, 0, 0), kf(2.0, 0.3, 0, 0)],
    [JW]: [kf(0, 0.25, 0, 0), kf(1.0, 0.4, 0, 0), kf(2.0, 0.25, 0, 0)],
    [LW0]: [kf(0, 0, 0, 0.9), kf(1.0, 0, 0, 1.1), kf(2.0, 0, 0, 0.9)],
    [RW0]: [kf(0, 0, 0, -0.9), kf(1.0, 0, 0, -1.1), kf(2.0, 0, 0, -0.9)]
  }),
  avian_squawk_angry: _aClip('avian_squawk_angry', 1.0, false, 'emotion-negative', ['暴躁', '聒噪', '叛逆'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.3, -0.2, 0, 0), kf(0.7, 0.1, 0, 0), kf(1.0, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.3, 0.45, 0, 0), kf(0.7, 0, 0, 0), kf(1.0, 0, 0, 0)]
  }),
  avian_sulking: _aClip('avian_sulking', 3.5, true, 'emotion-negative', ['傲娇', '孤傲', '冷漠'], {
    [HD]: [kf(0, 0.1, 0.5, 0), kf(1.75, 0.12, 0.55, 0), kf(3.5, 0.1, 0.5, 0)],
    [LW0]: [kf(0, 0, 0, 0.1), kf(1.75, 0, 0, 0.15), kf(3.5, 0, 0, 0.1)]
  })
}
