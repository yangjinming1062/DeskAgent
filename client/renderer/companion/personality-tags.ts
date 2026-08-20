import type { RigType } from './3d/rig'

/** 按骨骼类型分类的初始种子词汇表（客户端与后端共享常量）。
 *  包含通用维度与 7 大骨骼类型特有维度，共 150+ 种子标签。
 *  LLM 可产出词汇表外的标签 — 这些标签被保留，并触发专属动画/音频自动生成。 */
export const PERSONALITY_TAG_SEED_BY_RIG = {
  common: [
    '温顺',
    '警惕',
    '敏锐',
    '暴躁',
    '好奇',
    '沉稳',
    '灵动',
    '威严',
    '幼态',
    '迟钝',
    '好斗',
    '懒散',
    '忠诚',
    '狡黠',
    '胆小',
    '敏捷',
    '神秘',
    '亲人',
    '独立',
    '贪吃'
  ],
  biped: [
    // 活力
    '活泼',
    '好动',
    '元气',
    '安静',
    '慵懒',
    '热血',
    '文静',
    // 温度
    '温柔',
    '温婉',
    '体贴',
    '暖心',
    '冷漠',
    '高冷',
    '清冷',
    '孤僻',
    // 趣味
    '俏皮',
    '调皮',
    '搞怪',
    '呆萌',
    '软萌',
    '中二',
    '幽默',
    '腹黑',
    // 态度
    '傲娇',
    '毒舌',
    '霸道',
    '强势',
    '叛逆',
    '内敛',
    '严肃',
    // 魅力
    '妖娆',
    '妩媚',
    '性感',
    '清纯',
    '仙气',
    '贵气',
    '优雅',
    // 情感
    '阳光',
    '开朗',
    '忧郁',
    '敏感',
    '神经质',
    '细腻',
    '多愁善感',
    // 才智
    '理性',
    '冷静',
    '知性',
    '聪明',
    '博学',
    '严谨',
    // 社交
    '粘人',
    '害羞',
    '社恐',
    '社牛',
    '体面',
    '随和'
  ],
  quadruped: [
    '护主',
    '撒娇',
    '狂野',
    '贪玩',
    '拆家',
    '顺从',
    '凶猛',
    '护食',
    '捕猎',
    '摇尾',
    '欢腾',
    '憨厚',
    '警戒',
    '领地意识',
    '爱抚',
    '温顺可爱',
    '精力充沛',
    '机警敏捷'
  ],
  avian: [
    '高傲',
    '翱翔',
    '啼鸣',
    '聒噪',
    '俯冲',
    '求偶',
    '高贵',
    '灵巧',
    '孤傲',
    '机敏',
    '轻盈',
    '展翅',
    '鸣啭',
    '华丽',
    '警觉锐利',
    '从容不迫',
    '羽翼丰满'
  ],
  serpentine: [
    '冷酷',
    '潜伏',
    '致命',
    '蜕变',
    '缠绕',
    '森冷',
    '剧毒',
    '幽暗',
    '诡谲',
    '隐忍',
    '冰冷',
    '吐信',
    '盘踞',
    '阴翳',
    '迅捷突袭',
    '神秘莫测'
  ],
  aquatic: [
    '悠游',
    '静谧',
    '深邃',
    '跃动',
    '浮游',
    '群居',
    '洄游',
    '幻彩',
    '纯净',
    '游弋',
    '吐泡',
    '摆尾',
    '空灵',
    '波澜不惊',
    '如鱼得水',
    '灵波荡漾'
  ],
  hexapod: [
    '勤劳',
    '秩序',
    '机械',
    '群集',
    '工蜂',
    '蛰伏',
    '探索',
    '坚韧',
    '服从',
    '狂躁',
    '筑巢',
    '拟态',
    '触角敏锐',
    '冷酷高效',
    '甲壳坚硬'
  ],
  octopod: [
    '多智',
    '伪装',
    '莫测',
    '怪诞',
    '克苏鲁',
    '多面',
    '诡异',
    '探知',
    '喷墨',
    '触手灵动',
    '不可名状',
    '洞察',
    '狡诈多端',
    '深海潜行',
    '柔韧变幻'
  ]
} as const

/** 扁平合并的所有种子标签（去重） */
export const PERSONALITY_TAG_SEED: readonly string[] = Array.from(
  new Set(Object.values(PERSONALITY_TAG_SEED_BY_RIG).flat())
)

export type PersonalityTag = string

/** 获取特定骨骼类型的推荐标签集合（通用 + 该 rig 特有） */
export function getSeedTagsForRig(rigType?: RigType | string | null): readonly string[] {
  const common = PERSONALITY_TAG_SEED_BY_RIG.common

  const specific =
    rigType && rigType in PERSONALITY_TAG_SEED_BY_RIG
      ? PERSONALITY_TAG_SEED_BY_RIG[rigType as keyof typeof PERSONALITY_TAG_SEED_BY_RIG]
      : PERSONALITY_TAG_SEED_BY_RIG.biped

  return Array.from(new Set([...common, ...specific]))
}
