import { useStore } from '@nanostores/react'
import { act, cleanup, render } from '@testing-library/react'
import type React from 'react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { MessageBubble } from './chat-dock-message-bubble'
import {
  $chatMessageBodies,
  $chatMessageList,
  appendAssistantDelta,
  beginAssistantMessage,
  clearChat,
  pushUserMessage
} from './chat-store'

// 记录组件渲染次数。
function RenderCounter({ testId }: { testId: string }): React.JSX.Element {
  const ref = useRef(0)
  ref.current += 1

  return (
    <span data-renders={ref.current} data-testid={testId}>
      {ref.current}
    </span>
  )
}

// 包裹 MessageBubble 并监听自身 key 的渲染次数。
function BubbleHarness({ testId }: { testId: string }): React.JSX.Element {
  useStore($chatMessageBodies, { keys: [testId], deps: [testId] })
  const list = $chatMessageList.get()
  const item = list.find(m => m.id === testId)

  if (!item) {
    return <RenderCounter testId={`counter-${testId}`} />
  }

  return (
    <div>
      <RenderCounter testId={`counter-${testId}`} />
      <MessageBubble message={item} />
    </div>
  )
}

function readRenders(testId: string): number {
  const el = document.querySelector(`[data-testid="counter-${testId}"]`)

  return el ? Number(el.getAttribute('data-renders')) : 0
}

describe('chat-store streaming render isolation', () => {
  beforeEach(() => clearChat())
  afterEach(() => cleanup())

  it('delta updates only the streaming bubble, not history bubbles', () => {
    pushUserMessage('你好')
    beginAssistantMessage()

    const list = $chatMessageList.get()
    const userId = list[0]!.id
    const assistantId = list[1]!.id

    render(
      <div>
        <BubbleHarness testId={userId} />
        <BubbleHarness testId={assistantId} />
      </div>
    )

    const userBefore = readRenders(userId)
    const assistantBefore = readRenders(assistantId)

    act(() => {
      appendAssistantDelta('嗨')
      appendAssistantDelta('，我是小光')
      appendAssistantDelta('！')
    })

    expect(readRenders(userId)).toBe(userBefore)
    expect(readRenders(assistantId)).toBeGreaterThan(assistantBefore)
  })

  it('does not invoke the streaming bubble renderer on finalize-only mutations of earlier bubbles', () => {
    pushUserMessage('u1')
    appendAssistantDelta('a1')
    beginAssistantMessage()
    appendAssistantDelta('a2')

    const list = $chatMessageList.get()
    const firstBubbleId = list[0]!.id
    const firstAssistantId = list[1]!.id
    const secondAssistantId = list[2]!.id

    render(
      <div>
        <BubbleHarness testId={firstBubbleId} />
        <BubbleHarness testId={firstAssistantId} />
        <BubbleHarness testId={secondAssistantId} />
      </div>
    )

    const firstUserRenders = readRenders(firstBubbleId)
    const firstAssistantRenders = readRenders(firstAssistantId)
    const secondAssistantRenders = readRenders(secondAssistantId)

    act(() => {
      appendAssistantDelta('more')
    })

    expect(readRenders(firstBubbleId)).toBe(firstUserRenders)
    expect(readRenders(firstAssistantId)).toBe(firstAssistantRenders)
    expect(readRenders(secondAssistantId)).toBeGreaterThan(secondAssistantRenders)
  })

  it('renders nothing for a MessageBubble whose id has no body (defensive)', () => {
    render(<MessageBubble message={{ id: 'ghost', role: 'assistant' }} />)
    expect(document.body.textContent).toBe('')
  })
})
