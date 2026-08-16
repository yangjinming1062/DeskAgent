import { beforeEach, describe, expect, it } from 'vitest'

import {
  $chatMessages,
  appendAssistantDelta,
  beginAssistantMessage,
  clearChat,
  clearPendingPrompts,
  drainPendingPrompts,
  finalizeAssistantMessage,
  pushPendingPrompt,
  pushUserMessage,
  setAssistantError,
  setAssistantTool
} from './chat-store'

beforeEach(() => clearChat())

describe('chat store streaming', () => {
  it('appends user then streaming assistant deltas and finalizes', () => {
    pushUserMessage('你好')
    beginAssistantMessage()
    appendAssistantDelta('嗨')
    appendAssistantDelta('，我是小光')
    finalizeAssistantMessage('嗨，我是小光')

    const msgs = $chatMessages.get()
    expect(msgs).toHaveLength(2)
    expect(msgs[0]).toMatchObject({ role: 'user', text: '你好' })
    expect(msgs[1]).toMatchObject({ role: 'assistant', text: '嗨，我是小光', streaming: false })
  })

  it('marks the streaming assistant message with the running tool', () => {
    beginAssistantMessage()
    setAssistantTool('terminal')
    expect($chatMessages.get().at(-1)?.toolName).toBe('terminal')
    setAssistantTool(null)
    expect($chatMessages.get().at(-1)?.toolName).toBeNull()
  })

  it('finalizes a prior streaming segment when a new one begins (tool rounds)', () => {
    beginAssistantMessage()
    appendAssistantDelta('先查一下')
    beginAssistantMessage() // second assistant segment after a tool round
    appendAssistantDelta('结果是')

    const msgs = $chatMessages.get()
    expect(msgs).toHaveLength(2)
    expect(msgs[0]?.streaming).toBe(false)
    expect(msgs[1]?.streaming).toBe(true)
  })

  it('surfaces an error on the streaming assistant message', () => {
    beginAssistantMessage()
    appendAssistantDelta('一半')
    setAssistantError('连接断了')
    const last = $chatMessages.get().at(-1)
    expect(last?.error).toBe('连接断了')
    expect(last?.streaming).toBe(false)
  })

  it('prunes empty assistant message on finalize (affect-only ghost bubble prevention)', () => {
    pushUserMessage('惹你生气')
    beginAssistantMessage()
    // No text delta arrived (affect-only response)
    finalizeAssistantMessage('')
    const msgs = $chatMessages.get()
    expect(msgs).toHaveLength(1)
    expect(msgs[0]?.role).toBe('user')
  })

  it('manages pending prompt batch correctly', () => {
    clearPendingPrompts()
    pushPendingPrompt({ text: '消息1' })
    pushPendingPrompt({ text: '消息2', attachments: ['a.png'] })
    const drained = drainPendingPrompts()
    expect(drained).toHaveLength(2)
    expect(drained[0].text).toBe('消息1')
    expect(drained[1].attachments).toEqual(['a.png'])
    expect(drainPendingPrompts()).toHaveLength(0)
  })
})
