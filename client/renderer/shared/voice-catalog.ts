export interface VoiceOption {
  id: string
  label: string
  gender: string
  language: string
  tags: readonly string[]
  description: string
  // 供应商提供的设计音色说明，展示在枢纽层的画廊中。
  voice_design_guide?: string
}

export const LANGUAGE_LABELS: Record<string, string> = {
  zh: '中文',
  en: '英文',
  multi: '多语言',
  '': '通用'
}

export const GENDER_OPTIONS: { id: string; label: string }[] = [
  { id: '', label: '全部' },
  { id: 'female', label: '女声' },
  { id: 'male', label: '男声' },
  { id: 'neutral', label: '中性' }
]

// MiMoTTSProvider.synthesize 用来把设计音色编码成单个字符串的前
// 前缀（`mimo_voicedesign:<prompt>`）。两处会识别它：
// 渲染层的合法性检查（companion/voice-validity.ts）和主进程的 TTS 路由
// （media.cjs）——后者无论用户偏好本地还是云端，都必须把设计音色强制走云。
export const VOICEDESIGN_PREFIX = 'mimo_voicedesign:'

export type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>
