import { beforeEach, describe, expect, it } from 'vitest'

import { $chatMessages, clearChat, setChatSession } from './chat-store'
import { $spriteState } from './companion-store'
import { handleCompanionEvent } from './events'

beforeEach(() => {
  clearChat()
  setChatSession('main-1')
  $chatMessages.set([])
})

describe('handleCompanionEvent session filter', () => {
  it('ignores message.start emitted on a non-active session (cron)', () => {
    handleCompanionEvent({ type: 'message.start', session_id: 'cron-1', payload: {} })

    expect($chatMessages.get()).toHaveLength(0)
  })

  it('processes message.start on the active session', () => {
    handleCompanionEvent({ type: 'message.start', session_id: 'main-1', payload: {} })

    expect($chatMessages.get()).toHaveLength(1)
    expect($chatMessages.get()[0]).toMatchObject({ role: 'assistant', streaming: true })
  })

  it('ignores message.complete with mismatched session even when text arrives', () => {
    handleCompanionEvent({ type: 'message.complete', session_id: 'cron-1', payload: { text: 'cron 文本' } })

    expect($chatMessages.get()).toHaveLength(0)
  })

  it('passes WSEvent-driven events through the session filter', () => {
    // companion.message has no session_id and must reach its case branch:
    // the affect lands on the sprite, while the proactive bubble path
    // creates no chat-streaming message.
    $spriteState.set('idle')
    handleCompanionEvent({ type: 'companion.message', payload: { text: '今天好', affect: { emotion: 'happy' } } })

    expect($spriteState.get()).toBe('emotional')
    expect($chatMessages.get().every(m => m.role !== 'assistant' || m.streaming !== true)).toBe(true)
  })
})
