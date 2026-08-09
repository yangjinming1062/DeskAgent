import type { ClipDef, Keyframe } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface SerpentineBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  tail: readonly string[]
}

export const TRIPO_SERPENTINE_BONES: SerpentineBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Spine2', 'Spine3', 'Spine4', 'Spine5', 'Spine6', 'Spine7', 'Spine8', 'Spine9', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  tail: ['Tail1', 'Tail2', 'Tail3', 'Tail4', 'Tail5']
}

function kf(t: number, x: number, y: number, z: number): Keyframe {
  return { t, r: [x, y, z] as const }
}

const S0 = TRIPO_SERPENTINE_BONES.spine[0]
const S1 = TRIPO_SERPENTINE_BONES.spine[1]
const S2 = TRIPO_SERPENTINE_BONES.spine[2]
const S3 = TRIPO_SERPENTINE_BONES.spine[3]
const S4 = TRIPO_SERPENTINE_BONES.spine[4]
const HD = TRIPO_SERPENTINE_BONES.head
const JW = TRIPO_SERPENTINE_BONES.jaw
const TL0 = TRIPO_SERPENTINE_BONES.tail[0]
const TL1 = TRIPO_SERPENTINE_BONES.tail[1]

function _sClip(
  name: string,
  duration: number,
  loop: boolean,
  category: ClipDef['category'],
  tags: readonly string[],
  tracks: Record<string, Keyframe[]>
): ClipDef {
  return { name, duration, loop, category, tags, tracks }
}

export const SERPENTINE_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(S0, HD),

  // ── States & Locomotion ──
  serpent_idle_coil: _sClip('serpent_idle_coil', 4, true, 'state', ['盘踞', '静谧', '沉稳'], {
    [S0]: [kf(0, 0, 0.4, 0), kf(2, 0.02, 0.45, 0), kf(4, 0, 0.4, 0)],
    [S1]: [kf(0, 0, 0.5, 0), kf(2, 0.02, 0.52, 0), kf(4, 0, 0.5, 0)],
    [HD]: [kf(0, 0.1, 0, 0), kf(2, 0.15, 0.05, 0), kf(4, 0.1, 0, 0)]
  }),
  serpent_sleep_coil: _sClip('serpent_sleep_coil', 6, true, 'state', ['盘踞', '幽暗', '隐忍'], {
    [S0]: [kf(0, 0, 0.6, 0), kf(3, 0.02, 0.62, 0), kf(6, 0, 0.6, 0)],
    [HD]: [kf(0, 0.25, 0.1, 0), kf(3, 0.28, 0.12, 0), kf(6, 0.25, 0.1, 0)],
    [TL0]: [kf(0, 0, 0.4, 0), kf(3, 0, 0.45, 0), kf(6, 0, 0.4, 0)]
  }),
  serpent_slither: _sClip('serpent_slither', 1.6, true, 'locomotion', ['灵动', '冷酷', '敏捷'], {
    [S0]: [kf(0, 0, 0.4, 0), kf(0.8, 0, -0.4, 0), kf(1.6, 0, 0.4, 0)],
    [S1]: [kf(0, 0, -0.4, 0), kf(0.8, 0, 0.4, 0), kf(1.6, 0, -0.4, 0)],
    [S2]: [kf(0, 0, 0.4, 0), kf(0.8, 0, -0.4, 0), kf(1.6, 0, 0.4, 0)],
    [S3]: [kf(0, 0, -0.4, 0), kf(0.8, 0, 0.4, 0), kf(1.6, 0, -0.4, 0)],
    [HD]: [kf(0, 0, -0.15, 0), kf(0.8, 0, 0.15, 0), kf(1.6, 0, -0.15, 0)]
  }),
  serpent_slither_fast: _sClip('serpent_slither_fast', 0.8, true, 'locomotion', ['迅捷突袭', '敏锐', '敏捷'], {
    [S0]: [kf(0, 0, 0.6, 0), kf(0.4, 0, -0.6, 0), kf(0.8, 0, 0.6, 0)],
    [S1]: [kf(0, 0, -0.6, 0), kf(0.4, 0, 0.6, 0), kf(0.8, 0, -0.6, 0)],
    [S2]: [kf(0, 0, 0.6, 0), kf(0.4, 0, -0.6, 0), kf(0.8, 0, 0.6, 0)],
    [HD]: [kf(0, 0, -0.2, 0), kf(0.4, 0, 0.2, 0), kf(0.8, 0, -0.2, 0)]
  }),
  serpent_strike: _sClip('serpent_strike', 1.0, false, 'locomotion', ['致命', '迅捷突袭', '剧毒', '凶猛'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.3, -0.4, 0, 0), kf(0.6, 0.6, 0, 0), kf(1.0, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.3, 0.3, 0, 0), kf(0.6, -0.5, 0, 0), kf(1.0, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.5, 0.6, 0, 0), kf(0.8, 0, 0, 0), kf(1.0, 0, 0, 0)]
  }),
  serpent_coil_tight: _sClip('serpent_coil_tight', 2.0, false, 'locomotion', ['盘踞', '缠绕', '隐忍'], {
    [S0]: [kf(0, 0, 0, 0), kf(1.0, 0, 0.8, 0), kf(2.0, 0, 0.8, 0)],
    [S1]: [kf(0, 0, 0, 0), kf(1.0, 0, 0.9, 0), kf(2.0, 0, 0.9, 0)]
  }),
  serpent_uncoil: _sClip('serpent_uncoil', 2.0, false, 'locomotion', ['潜伏', '神秘', '优雅'], {
    [S0]: [kf(0, 0, 0.8, 0), kf(2.0, 0, 0, 0)],
    [S1]: [kf(0, 0, 0.9, 0), kf(2.0, 0, 0, 0)],
    [HD]: [kf(0, 0.2, 0, 0), kf(2.0, 0, 0, 0)]
  }),

  // ── Interaction & Intimate ──
  serpent_tongue_flick: _sClip('serpent_tongue_flick', 1.5, true, 'interaction', ['吐信', '机敏', '好奇'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.3, 0.05, 0.05, 0), kf(0.7, 0.05, -0.05, 0), kf(1.1, 0.05, 0.05, 0), kf(1.5, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.3, 0.1, 0, 0), kf(0.7, 0.1, 0, 0), kf(1.1, 0.1, 0, 0), kf(1.5, 0, 0, 0)]
  }),
  serpent_curl_around: _sClip('serpent_curl_around', 3.5, true, 'intimate', ['缠绕', '护主', '依赖', '粘人'], {
    [S0]: [kf(0, 0, 0.5, 0), kf(1.75, 0.02, 0.55, 0), kf(3.5, 0, 0.5, 0)],
    [S1]: [kf(0, 0, 0.6, 0), kf(1.75, 0.02, 0.65, 0), kf(3.5, 0, 0.6, 0)],
    [HD]: [kf(0, 0.1, 0.1, 0), kf(1.75, 0.12, 0.15, 0), kf(3.5, 0, 0.1, 0)]
  }),
  serpent_nuzzle: _sClip('serpent_nuzzle', 2.0, false, 'intimate', ['温柔', '亲人', '温顺'], {
    [HD]: [kf(0, 0, 0, 0), kf(1.0, 0.08, 0.25, 0.1), kf(2.0, 0, 0, 0)],
    [S0]: [kf(0, 0, 0, 0), kf(1.0, 0, 0.1, 0), kf(2.0, 0, 0, 0)]
  }),
  serpent_head_rest: _sClip('serpent_head_rest', 3.5, true, 'intimate', ['依偎', '温顺', '信任'], {
    [HD]: [kf(0, 0.2, 0, 0), kf(1.75, 0.22, 0.02, 0), kf(3.5, 0.2, 0, 0)],
    [S0]: [kf(0, 0.05, 0, 0), kf(1.75, 0.06, 0, 0), kf(3.5, 0.05, 0, 0)]
  }),

  // ── Positive & Ritual ──
  serpent_content_coil: _sClip('serpent_content_coil', 3.5, true, 'emotion-positive', ['森冷', '优雅', '从容不迫'], {
    [S0]: [kf(0, 0, 0.3, 0), kf(1.75, 0.02, 0.35, 0), kf(3.5, 0, 0.3, 0)],
    [HD]: [kf(0, -0.05, 0, 0), kf(1.75, -0.08, 0, 0), kf(3.5, -0.05, 0, 0)]
  }),
  serpent_sway_dance: _sClip('serpent_sway_dance', 2.5, true, 'emotion-positive', ['神秘', '诡谲', '灵动', '华丽'], {
    [S0]: [kf(0, -0.2, 0.3, 0), kf(1.25, -0.2, -0.3, 0), kf(2.5, -0.2, 0.3, 0)],
    [HD]: [kf(0, 0.1, -0.2, 0), kf(1.25, 0.1, 0.2, 0), kf(2.5, 0.1, -0.2, 0)]
  }),
  serpent_shed_skin: _sClip('serpent_shed_skin', 3.0, false, 'ritual', ['蜕变', '神秘莫测', '新生'], {
    [S0]: [kf(0, 0, 0.3, 0), kf(1.5, 0, -0.3, 0), kf(3.0, 0, 0.3, 0)],
    [S1]: [kf(0, 0, -0.3, 0), kf(1.5, 0, 0.3, 0), kf(3.0, 0, -0.3, 0)],
    [HD]: [kf(0, 0.2, 0, 0), kf(1.5, 0.25, 0, 0), kf(3.0, 0.2, 0, 0)]
  }),

  // ── Negative Emotion & Threat ──
  serpent_hiss: _sClip('serpent_hiss', 1.8, false, 'emotion-negative', ['森冷', '警戒', '冷酷'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.5, -0.15, 0, 0), kf(1.8, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.5, 0.35, 0, 0), kf(1.8, 0, 0, 0)],
    [S0]: [kf(0, 0, 0, 0), kf(0.5, -0.1, 0, 0), kf(1.8, 0, 0, 0)]
  }),
  serpent_angry_hiss: _sClip('serpent_angry_hiss', 2.0, true, 'emotion-negative', ['剧毒', '凶猛', '致命', '暴躁'], {
    [S0]: [kf(0, -0.3, 0, 0), kf(1.0, -0.35, 0, 0), kf(2.0, -0.3, 0, 0)],
    [HD]: [kf(0, 0.2, 0, 0), kf(1.0, 0.25, 0, 0), kf(2.0, 0.2, 0, 0)],
    [JW]: [kf(0, 0.3, 0, 0), kf(1.0, 0.5, 0, 0), kf(2.0, 0.3, 0, 0)]
  }),
  serpent_coil_defend: _sClip('serpent_coil_defend', 2.5, true, 'emotion-negative', ['潜伏', '警惕', '冷酷'], {
    [S0]: [kf(0, 0, 0.7, 0), kf(1.25, 0, 0.75, 0), kf(2.5, 0, 0.7, 0)],
    [HD]: [kf(0, -0.1, 0, 0), kf(1.25, -0.15, 0, 0), kf(2.5, -0.1, 0, 0)]
  })
}
