import { atom, type WritableAtom } from 'nanostores'

import { persistBoolean, persistString, storedBoolean, storedString } from '@/shared/lib/storage'

// 响应模式控制伙伴在 Chat 模式下如何回复（DESIGN §6.1 响应模式）。
// 语音通话模式始终是语音，与此设置无关。
export type ResponseMode = 'text' | 'voice'

export const $companionVoiceId = atom<string>(storedString('da.companion.voiceId') ?? '')
export const $responseMode = atom<ResponseMode>((storedString('da.companion.responseMode') as ResponseMode) ?? 'text')

export function setCompanionVoiceId(voice: string): void {
  $companionVoiceId.set(voice)
  persistString('da.companion.voiceId', voice || null)
}

export function setResponseMode(mode: ResponseMode): void {
  $responseMode.set(mode)
  persistString('da.companion.responseMode', mode)
}

// 由 localStorage 支撑的布尔开关——供伙伴设置里的 LLM 驱动反应开关使用。
// 每个开关的持久化都由工厂函数原子创建，以后新增只需一行。
interface BooleanPref {
  $atom: WritableAtom<boolean>
  set: (value: boolean) => void
}

function makeBooleanPref(key: string, fallback: boolean): BooleanPref {
  const $atom = atom<boolean>(storedBoolean(key, fallback))

  return {
    $atom,
    set(value: boolean): void {
      $atom.set(value)
      persistBoolean(key, value)
    }
  }
}

const llmReactionsPref = makeBooleanPref('da.companion.llmReactions', true)
const llmAffectPref = makeBooleanPref('da.companion.llmAffect', true)
const llmAutonomyPref = makeBooleanPref('da.companion.llmAutonomy', true)

// 语音通话模式下双向字幕显示开关（DESIGN §6.1「双向字幕可切换」）。
const subtitlesPref = makeBooleanPref('da.companion.subtitles', true)

export const $llmReactions = llmReactionsPref.$atom
export const $llmAffect = llmAffectPref.$atom
export const $llmAutonomy = llmAutonomyPref.$atom
export const $subtitles = subtitlesPref.$atom

export const setLlmReactions = llmReactionsPref.set
export const setLlmAffect = llmAffectPref.set
export const setLlmAutonomy = llmAutonomyPref.set
export const setSubtitles = subtitlesPref.set
