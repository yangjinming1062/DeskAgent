import type { SpriteEmotion, SpriteStateName } from '../companion-store'

// Free-form Chinese semantics for the static-sprite album. The backend LLM
// matches these against album tags (or authors a generation prompt on miss),
// so requests are deliberately open-ended rather than enum-keyed — an unknown
// LLM-invented emotion still resolves via the generic fallback clause.

const STATE_SEMANTICS: Record<SpriteStateName, string> = {
  idle: '自然放松地站立，看向观众，温和的神态',
  listening: '微微侧耳倾听，神情专注',
  thinking: '手托下巴思考，微微歪头',
  speaking: '张嘴说话，双手做出表达手势',
  working: '专注忙碌，手持工具或对着笔记本操作',
  emotional: '情绪外露的反应神态',
  sleeping: '闭眼安睡，头顶冒出泡泡',
  interacting: '被戳到时活泼可爱的反应',
  disconnected: '低头犯困打盹的等待神态'
}

const EMOTION_SEMANTICS: Record<string, string> = {
  happy: '开心地笑',
  sad: '难过的表情',
  surprised: '惊讶地睁大眼睛',
  excited: '兴奋地雀跃',
  confused: '疑惑不解',
  concerned: '关切担忧',
  shy: '害羞脸红',
  proud: '骄傲自豪',
  grateful: '感激地微笑',
  playful: '顽皮地做鬼脸',
  bored: '百无聊赖',
  lonely: '孤单委屈',
  sleepy: '困倦地打哈欠',
  curious: '好奇地探头张望',
  embarrassed: '尴尬地挠头',
  apologetic: '不好意思地道歉',
  pout: '气鼓鼓地噘嘴傲娇',
  angry: '生闷气微怒',
  smug: '得意洋洋的小得意',
  scared: '受惊害怕',
  relieved: '如释重负地松了一口气'
}

/** The first-priority image: what static mode shows while it engages. */
export const WAITING_REQUEST = '安静站立等待的全身立绘，中性微笑，双手自然下垂'

export function semanticRequestFor(state: SpriteStateName, emotion: SpriteEmotion | null): string {
  const emotionClause = emotion ? `，带着${EMOTION_SEMANTICS[emotion] ?? `表现出 ${emotion} 的情绪`}的神情` : ''

  return `${STATE_SEMANTICS[state]}${emotionClause}，全身立绘`
}
