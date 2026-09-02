import {
  hydrateChatMessages,
  markAssistantTerminal,
  type PendingAttachment,
  pushStatusPill
} from '@/companion/chat-store'
import { ensureChatSession } from '@/companion/session-list-store'
import {
  type SlashCommandResultPayload,
  SpiritAgentRpcError,
  SpiritAgentRpcErrorCode
} from '@/shared/lib/gateway-protocol'
import { fuzzyFilterCommands, type SlashCommandMeta } from '@/shared/lib/slash-commands'
import type { SessionMessage } from '@/shared/types/spiritagent'

function slashErrorToMessage(err: unknown): string {
  if (err instanceof SpiritAgentRpcError) {
    if (err.code === SpiritAgentRpcErrorCode.SlashConfirmRequired) {
      return '该命令需要二次确认'
    }

    if (err.code === SpiritAgentRpcErrorCode.SlashBusy) {
      return '请先停止当前生成'
    }

    if (err.code === SpiritAgentRpcErrorCode.SlashGeneric) {
      return '命令执行失败'
    }

    if (err.code === SpiritAgentRpcErrorCode.InvalidParams) {
      const suggestions = (err.data as { suggestions?: string[] } | undefined)?.suggestions

      if (suggestions?.length) {
        return `未知命令。可选: ${suggestions.map(s => `/${s}`).join(', ')}`
      }

      return '未知命令'
    }
  }

  const msg = err instanceof Error ? err.message : String(err)

  return msg || '命令执行失败'
}

/**
 * 弹层是否应该拦截当前文本：避免「边发图片边清空」歧义。
 * 与 send() 的拦截分支共用同一道闸，保证弹层与兜底路径行为一致。
 */
function slashPreCheck(pending: PendingAttachment | null, sending: boolean): string | null {
  if (sending) {
    return '上一条命令还在执行中'
  }

  if (pending) {
    return '请先发送或取消附件再执行命令'
  }

  return null
}

/**
 * 单条 slash 命令的执行入口：confirm → RPC → hydrate/pill → 错误映射。所有三处调用方
 * （send() 兜底路径、textarea onKeyDown 选中、SlashCommandPopover 点击）都走这里。
 *
 * 需确认的命令每次调用都会弹 window.confirm——PROTOCOL §1.9 明确要求前端必须弹，
 * 即便弹层/Enter 路径已经把意图表达得很清楚。
 */
async function executeSlashCommand(
  cmd: SlashCommandMeta,
  args: string[],
  opts: {
    onFinish?: () => void
    onStart?: () => void
    requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  }
): Promise<void> {
  const { requestGateway, onStart, onFinish } = opts

  if (cmd.requiresConfirmation && !window.confirm(`执行 /${cmd.name}？该操作会影响历史消息。`)) {
    return
  }

  try {
    onStart?.()

    const sid = await ensureChatSession()

    const result = await requestGateway<SlashCommandResultPayload>('command.dispatch', {
      args,
      command: cmd.name,
      confirmed: true,
      session_id: sid
    })

    const r = result.result

    if (r.status === 'ok') {
      // hydrate=true 时，payload.messages 已包含服务端写入的 status_cleared / compress_summary
      // marker 行——前端 hydrateChatMessages 后再 pushStatusPill 会产生重复 pill，
      // 所以 hydrate 路径只更新消息列表，不再追加 status_command_result。
      if (r.hydrate && r.payload) {
        const raw = (r.payload as { messages?: unknown }).messages

        if (Array.isArray(raw)) {
          hydrateChatMessages(raw as SessionMessage[])
        }
      } else {
        pushStatusPill('status_command_result', r.message)
      }
    } else {
      markAssistantTerminal({ error: r.message })
    }
  } catch (err) {
    markAssistantTerminal({ error: slashErrorToMessage(err) })
  } finally {
    onFinish?.()
  }
}

export { executeSlashCommand, slashErrorToMessage, slashPreCheck }

// Re-export fuzzy filter from shared — slash popover uses it too.
export { fuzzyFilterCommands }
