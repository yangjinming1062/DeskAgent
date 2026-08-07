// Single source of truth for the persona/role onboarding preset chips. The
// three UI surfaces — onboarding wizard (onboarding/onboarding-flow.tsx),
// direct persona editor (persona-editor.tsx), and conversational retune
// wizard (persona-retune.tsx) — used to redefine the same arrays verbatim.
// Drift here means the user sees a different chip in onboarding than in the
// retune wizard, which is a worse experience than the three lines of indirection
// cost.
//
// Frozen tuples guard against accidental mutation; readonly arrays keep the
// caller-side typing strict.

export const ROLE_PRESETS = ['爱人', '秘书', '专属管家', '无话不谈的朋友'] as const
export const PERSONALITY_PRESETS = ['温柔体贴', '活泼好动', '冷静理性', '毒舌傲娇'] as const
export const SPECIES_PRESETS = ['人类', '灵兽', '精灵', '机甲', '幻形'] as const
export const CHARACTER_GENDER_PRESETS = ['女', '男', '其他', '不指定'] as const
export const APPEARANCE_PRESETS = ['优雅古典', '现代利落', '萌系可爱', '冷酷暗黑'] as const
export const SPEAKING_STYLE_PRESETS = ['温柔亲切', '俏皮带点小傲娇', '沉稳简洁', '轻快活泼', '专业干练'] as const
export const VOICE_PRESETS = ['温柔少女音', '沉稳男声', '活泼正太', '清冷御姐'] as const
export const USER_AGE_BUCKET_PRESETS = ['18 以下', '18-25', '26-35', '36-50', '50+'] as const
export const USER_GENDER_PRESETS = ['女', '男', '其他', '不愿说'] as const

export type RolePreset = (typeof ROLE_PRESETS)[number]
export type PersonalityPreset = (typeof PERSONALITY_PRESETS)[number]
export type SpeciesPreset = (typeof SPECIES_PRESETS)[number]
export type CharacterGenderPreset = (typeof CHARACTER_GENDER_PRESETS)[number]
export type AppearancePreset = (typeof APPEARANCE_PRESETS)[number]
export type SpeakingStylePreset = (typeof SPEAKING_STYLE_PRESETS)[number]
export type VoicePreset = (typeof VOICE_PRESETS)[number]
export type UserAgeBucketPreset = (typeof USER_AGE_BUCKET_PRESETS)[number]
export type UserGenderPreset = (typeof USER_GENDER_PRESETS)[number]
