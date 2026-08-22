import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $expressions } from '@/companion/3d/model-store'
import { ChatDock } from '@/companion/chat-dock'
import { resetChatMessages } from '@/companion/chat-store'
import { $spriteEmotion, $spriteState } from '@/companion/companion-store'
import { $expressionAvatar, resetExpressionAvatars } from '@/companion/expression-avatar/expression-avatar-store'
import { $portraitUrl } from '@/companion/portrait-store'
import { $gatewayState } from '@/shared/store/gateway'

vi.mock('@/companion/boot/use-gateway-request', () => ({
  useGatewayRequest: () => ({
    requestGateway: vi.fn().mockResolvedValue({})
  })
}))

const avatarImg = (): HTMLImageElement => screen.getByAltText('角色形象') as HTMLImageElement

describe('ChatDock', () => {
  beforeEach(() => {
    resetChatMessages()
    $gatewayState.set('open')
    $spriteState.set('idle')
    $spriteEmotion.set(null)
    $portraitUrl.set('http://test/avatar.png')
    $expressions.set([])
    resetExpressionAvatars()

    window.spiritagent = {
      ...window.spiritagent,
      api: vi.fn().mockResolvedValue({}),
      apiAsset: vi.fn().mockResolvedValue('data:image/png;base64,EXPR'),
      saveClipboardImage: vi.fn(),
      readFileDataUrl: vi.fn()
    } as unknown as typeof window.spiritagent
  })

  afterEach(() => {
    cleanup()
    $spriteEmotion.set(null)
    resetExpressionAvatars()
  })

  it('renders two-column layout with left emotion state and right chat panel', () => {
    const onClose = vi.fn()
    const onOpenVoiceCall = vi.fn()
    render(<ChatDock onClose={onClose} onOpenVoiceCall={onOpenVoiceCall} />)

    // 左侧视觉锚定元素：情绪状态与状态标签（无情绪时为平静）
    expect(screen.getByText('平静温和')).toBeDefined()
    expect(screen.getByText('当前情绪状态')).toBeDefined()
    expect(screen.getByText('在线陪伴')).toBeDefined()

    expect(avatarImg().src).toContain('avatar.png')

    // 右侧聊天面板元素
    expect(screen.getByPlaceholderText('输入消息，Enter 发送，Shift+Enter 换行')).toBeDefined()
    expect(screen.getByRole('button', { name: '关闭对话' })).toBeDefined()

    // 底部栏的语音通话按钮
    const voiceCallBtn = screen.getByRole('button', { name: '📞 通话' })
    expect(voiceCallBtn).toBeDefined()
    fireEvent.click(voiceCallBtn)
    expect(onOpenVoiceCall).toHaveBeenCalledTimes(1)
  })

  it('reflects dynamic emotion changes in visual anchor', () => {
    $spriteEmotion.set('curious')
    render(<ChatDock onClose={vi.fn()} />)

    expect(screen.getByText('充满好奇')).toBeDefined()
  })

  it('swaps the left-column avatar to the emotion image and back to the portrait', async () => {
    render(<ChatDock onClose={vi.fn()} />)
    $spriteEmotion.set('happy')

    await waitFor(() => expect($expressionAvatar.get()?.name).toBe('happy'))
    expect(avatarImg().src).toContain('EXPR')

    // 情绪瞬态结束 → 回退到半身像。
    $spriteEmotion.set(null)

    await waitFor(() => expect($expressionAvatar.get()).toBeNull())
    expect(avatarImg().src).toContain('avatar.png')
  })

  it('renders custom emotions from the registry with label and icon', () => {
    $expressions.set([
      {
        id: 1,
        name: 'tender_worry',
        label: '心疼担忧',
        valence: 'negative',
        description: '心疼又担忧地看着你',
        icon: '🥺',
        tags: []
      }
    ])
    $spriteEmotion.set('tender_worry')
    render(<ChatDock onClose={vi.fn()} />)

    expect(screen.getByText('心疼担忧')).toBeDefined()
    expect(screen.getByText('🥺')).toBeDefined()
  })

  it('falls back to a generic rendering for unknown emotion tokens', () => {
    $spriteEmotion.set('sparkly')
    render(<ChatDock onClose={vi.fn()} />)

    expect(screen.getByText('sparkly')).toBeDefined()
    expect(screen.getByText('💫')).toBeDefined()
  })

  it('reflects thinking state in visual anchor status badge', () => {
    $spriteState.set('thinking')
    render(<ChatDock onClose={vi.fn()} />)

    expect(screen.getByText('思考中…')).toBeDefined()
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(<ChatDock onClose={onClose} />)

    const closeBtn = screen.getByRole('button', { name: '关闭对话' })
    fireEvent.click(closeBtn)

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
