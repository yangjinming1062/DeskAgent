import { beforeEach, describe, expect, it } from 'vitest'

import {
  $chatMessageBodies,
  $chatMessageList,
  $chatStreamingTick,
  $lastAssistantStreaming,
  appendAssistantDelta,
  beginAssistantMessage,
  clearChat,
  clearPendingPrompts,
  drainPendingPrompts,
  finalizeAssistantMessage,
  hydrateChatMessages,
  pushPendingPrompt,
  pushUserMessage,
  setAssistantCancelled,
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

    const list = $chatMessageList.get()
    expect(list).toHaveLength(2)
    expect(list[0]).toMatchObject({ role: 'user' })
    expect(list[1]).toMatchObject({ role: 'assistant' })

    const bodies = $chatMessageBodies.get()
    expect(bodies[list[1]!.id]).toMatchObject({ text: '嗨，我是小光', streaming: false })
    expect($lastAssistantStreaming.get()).toBe(false)
  })

  it('marks the streaming assistant body with the running tool', () => {
    beginAssistantMessage()
    const id = $chatMessageList.get().at(-1)!.id
    setAssistantTool('terminal')
    expect($chatMessageBodies.get()[id]?.toolName).toBe('terminal')
    setAssistantTool(null)
    expect($chatMessageBodies.get()[id]?.toolName).toBeNull()
  })

  it('finalizes a prior streaming segment when a new one begins (tool rounds)', () => {
    beginAssistantMessage()
    appendAssistantDelta('先查一下')
    beginAssistantMessage() // 工具轮次之后的第二个助手消息段
    appendAssistantDelta('结果是')

    const list = $chatMessageList.get()
    expect(list).toHaveLength(2)
    const bodies = $chatMessageBodies.get()
    expect(bodies[list[0]!.id]?.streaming).toBe(false)
    expect(bodies[list[1]!.id]?.streaming).toBe(true)
    expect(bodies[list[1]!.id]?.text).toBe('结果是')
  })

  it('appends a new assistant bubble when text arrives after a tool round', () => {
    // 多气泡：tool 完成后新片段不应被合并到上一段。
    beginAssistantMessage()
    appendAssistantDelta('先查一下')
    beginAssistantMessage()
    appendAssistantDelta('结果是42')

    const list = $chatMessageList.get()
    expect(list).toHaveLength(2)
    const bodies = $chatMessageBodies.get()
    // 第一段保留文本 + 标记 finalize
    expect(bodies[list[0]!.id]?.text).toBe('先查一下')
    expect(bodies[list[0]!.id]?.streaming).toBe(false)
    // 第二段承接后续文本
    expect(bodies[list[1]!.id]?.text).toBe('结果是42')
    expect(bodies[list[1]!.id]?.streaming).toBe(true)
    expect($lastAssistantStreaming.get()).toBe(true)
  })

  it('surfaces an error on the streaming assistant body', () => {
    beginAssistantMessage()
    appendAssistantDelta('一半')
    setAssistantError('连接断了')
    const id = $chatMessageList.get().at(-1)!.id
    const body = $chatMessageBodies.get()[id]
    expect(body?.error).toBe('连接断了')
    expect(body?.streaming).toBe(false)
    expect($lastAssistantStreaming.get()).toBe(false)
  })

  it('prunes empty assistant message on finalize (affect-only ghost bubble prevention)', () => {
    pushUserMessage('惹你生气')
    beginAssistantMessage()
    // 没有收到文本增量（仅 affect 的响应）
    finalizeAssistantMessage('')
    const list = $chatMessageList.get()
    expect(list).toHaveLength(1)
    expect(list[0]?.role).toBe('user')
    expect($lastAssistantStreaming.get()).toBe(false)
  })

  it('keeps the list reference stable across deltas', () => {
    pushUserMessage('你好')
    beginAssistantMessage()
    const ref = $chatMessageList.get()
    appendAssistantDelta('a')
    appendAssistantDelta('b')
    appendAssistantDelta('c')
    expect($chatMessageList.get()).toBe(ref)
    expect($chatStreamingTick.get()).toBeGreaterThanOrEqual(3)
  })

  it('bumps $chatStreamingTick only on deltas, not on finalize or hydrate', () => {
    pushUserMessage('hi')
    beginAssistantMessage()
    const tickAfterBegin = $chatStreamingTick.get()
    appendAssistantDelta('x')
    appendAssistantDelta('y')
    const tickAfterDelta = $chatStreamingTick.get()
    expect(tickAfterDelta).toBeGreaterThan(tickAfterBegin)
    finalizeAssistantMessage('xy')
    expect($chatStreamingTick.get()).toBe(tickAfterDelta)
  })

  it('handles cancellation on streaming and idle assistant bubbles', () => {
    pushUserMessage('测试取消')
    beginAssistantMessage()
    appendAssistantDelta('收到一小部分')
    setAssistantCancelled()

    const list = $chatMessageList.get()
    expect(list).toHaveLength(2)
    const assistantId = list[1]!.id
    expect($chatMessageBodies.get()[assistantId]).toMatchObject({
      text: '收到一小部分',
      cancelled: true,
      streaming: false
    })
    expect($lastAssistantStreaming.get()).toBe(false)
  })

  it('hydrates messages from backend and extracts multimodal content', () => {
    hydrateChatMessages([
      { role: 'user', content: JSON.stringify([{ type: 'input_text', text: '提取的文本' }]) } as never,
      { role: 'assistant', content: '助手文本', subtype: 'status_proactive' } as never
    ])

    const list = $chatMessageList.get()
    expect(list).toHaveLength(2)
    expect(list[0]).toMatchObject({ role: 'user' })
    expect(list[1]).toMatchObject({ role: 'assistant', subtype: 'status_proactive' })

    const bodies = $chatMessageBodies.get()
    expect(bodies[list[0]!.id]?.text).toBe('提取的文本')
    expect(bodies[list[1]!.id]?.text).toBe('助手文本')
    expect($lastAssistantStreaming.get()).toBe(false)
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
