// 把 ``onBack`` 的转移决策抽成纯函数,便于测试且隔离状态机逻辑。
// 输入是当前阶段 + qIndex + voiceStage + imageSealed,输出是下一个状态意图或 null(表示"无变化")。

type BackPhase =
  | 'q-character'
  | 'q-user'
  | 'voice'
  | 'fullbody'
  | 'portrait-choose'
  | 'portrait-avatar'
  | 'hatching'
  | 'finishing'
  | 'greeting'
type BackVoiceStage = 'describe' | 'catalog'

interface BackState {
  phase: BackPhase
  qIndex: number
  voiceStage: BackVoiceStage
  imageSealed: boolean
}

interface BackIntent {
  phase: BackPhase
  qIndex?: number
  voiceStage?: BackVoiceStage
}

/** 返回 null 表示 onBack 在该状态下不产生任何变化。形象锁死后所有返回路径都被截断。 */
export function computeBackTransition(state: BackState, characterQuestionsCount: number): BackIntent | null {
  if (state.imageSealed) {
    return null
  }

  if (state.phase === 'q-character') {
    return state.qIndex > 0 ? { phase: 'q-character', qIndex: state.qIndex - 1 } : null
  }

  if (state.phase === 'portrait-choose') {
    return { phase: 'q-character', qIndex: characterQuestionsCount - 1 }
  }

  if (state.phase === 'portrait-avatar') {
    return { phase: 'portrait-choose' }
  }

  if (state.phase === 'voice') {
    if (state.voiceStage === 'describe') {
      return { phase: 'q-character', qIndex: characterQuestionsCount - 1 }
    }

    return null
  }

  if (state.phase === 'q-user') {
    if (state.qIndex > 0) {
      return { phase: 'q-user', qIndex: state.qIndex - 1 }
    }

    return { phase: 'voice', voiceStage: 'catalog' }
  }

  return null
}
