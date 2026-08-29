// 客户端 Slash 命令元数据：权威源在服务端 services/chat/slash_commands.py 的 SLASH_COMMANDS
// 注册表，通过 WS RPC ``command.list`` 拉取并写进本文件的 atom —— 避免前后端两份数组手同步漂移。
//
// 启动流程（use-gateway-boot.ts）：gateway open 后调 fetchSlashCommandMeta() 写 atom；
// 拉取完成前的短暂窗口内，弹层为空（首期 2 条命令的延迟 <100ms 可忽略）。
//
// 服务端 dispatch 仍是唯一权威：前端 getLocalSlashMeta 只用于自动补全 UI 与 confirm 弹窗。

import { atom } from 'nanostores'

export interface SlashCommandMeta {
  /** 主名（小写，无前导 /）。 */
  name: string
  /** 别名数组（命中同一命令）。 */
  aliases: readonly string[]
  /** 给自动补全弹层与 /帮助 展示的描述。 */
  description: string
  /** 是否需要前端二次确认弹窗（companion / developer 等预设对话尤其重要）。 */
  requiresConfirmation: boolean
}

// 服务端 ``command.list`` 返回的原始条目。
interface ServerCommandEntry {
  name: string
  aliases?: readonly string[]
  description?: string
  requires_confirmation?: boolean
}

// 启动前为空，启动后由 fetchSlashCommandMeta 写入。
export const $slashCommandMeta = atom<readonly SlashCommandMeta[]>([])

export function setSlashCommandMeta(metas: readonly SlashCommandMeta[]): void {
  $slashCommandMeta.set(metas)
}

function normalizeServerEntry(entry: ServerCommandEntry): SlashCommandMeta {
  return {
    name: entry.name,
    aliases: entry.aliases ?? [],
    description: entry.description ?? '',
    requiresConfirmation: Boolean(entry.requires_confirmation)
  }
}

/** 按名（已剥离前导 /，小写）查 SlashCommandMeta；atom 未加载时返回 undefined。 */
export function getLocalSlashMeta(name: string): SlashCommandMeta | undefined {
  return $slashCommandMeta
    .get()
    .find(cmd => cmd.name === name.toLowerCase() || cmd.aliases.includes(name.toLowerCase()))
}

/** 列出所有命令（无别名重复），按 name 排序。 */
export function listLocalSlashCommands(): SlashCommandMeta[] {
  return [...$slashCommandMeta.get()].sort((a, b) => a.name.localeCompare(b.name))
}

export interface ParsedSlashInput {
  /** 命中命令的元数据；未识别时为 undefined。 */
  command: SlashCommandMeta | undefined
  /** 命中的主名（已剥离前导 /，小写）。未识别时为原始 token。 */
  name: string
  /** 命中的参数数组。 */
  args: string[]
}

/**
 * 解析用户输入文本：
 * - ``/foo`` / ``/foo a b`` → 命中命令 `foo`，args = ['a','b']
 * - ``/压缩``                → 别名命中 `compress`，args = []
 * - ``//注释``                → {command: undefined, ...}（以 `//` 开头的普通文本不视为命令）
 * - ``/path/to/file``        → {command: undefined, ...}（首 token 字符不是 ASCII 字母或中文）
 */
export function parseSlashInput(rawText: string): ParsedSlashInput | null {
  const trimmed = rawText.trim()

  if (!trimmed.startsWith('/')) {
    return null
  }

  // ``//`` 视为普通文本（注释 / 路径）。
  if (trimmed.startsWith('//')) {
    return null
  }

  const firstChar = trimmed.charAt(1)

  // 第二字符必须是 ASCII 字母或中文（CJK 基本区 0x4E00-0x9FFF）。
  if (!isCommandNameStart(firstChar)) {
    return null
  }

  const body = trimmed.slice(1)
  const spaceIdx = body.search(/\s/)
  const name = (spaceIdx === -1 ? body : body.slice(0, spaceIdx)).toLowerCase()

  const args =
    spaceIdx === -1
      ? []
      : body
          .slice(spaceIdx + 1)
          .split(/\s+/)
          .filter(Boolean)

  return {
    command: getLocalSlashMeta(name),
    name,
    args
  }
}

function isCommandNameStart(ch: string): boolean {
  if (!ch) {
    return false
  }

  // ASCII 字母
  const code = ch.charCodeAt(0)

  if ((code >= 0x41 && code <= 0x5a) || (code >= 0x61 && code <= 0x7a)) {
    return true
  }

  // CJK Unified Ideographs
  if (code >= 0x4e00 && code <= 0x9fff) {
    return true
  }

  return false
}

// --- 模糊匹配（弹层过滤） ---

/**
 * 模糊匹配打分：完全等于 100；前缀匹配按命中长度递减；子序列匹配按距离顺序得分。
 * 返回 0 表示不匹配（过滤掉），正值越大匹配越好。
 */
function fuzzyScore(query: string, target: string): number {
  if (!query) {
    return 100
  } // 空查询 → 全量

  const q = query.toLowerCase()
  const t = target.toLowerCase()

  if (t === q) {
    return 100
  }

  if (t.startsWith(q)) {
    return 80 - (t.length - q.length) * 2
  }

  // 子序列匹配：q 的所有字符按顺序出现在 t 中
  let qi = 0
  let lastMatch = -1
  let score = 0

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      score += ti - lastMatch === 1 ? 10 : 4 // 连续命中加权
      lastMatch = ti
      qi++
    }
  }

  if (qi < q.length) {
    return 0
  }

  return score
}

export interface ScoredSlashCommand {
  cmd: SlashCommandMeta
  score: number
  /** 实际匹配上的展示 token（name 或 alias），用于弹层显示。 */
  matchedKey: string
}

/**
 * 按 query 模糊过滤本地命令列表，按得分降序。
 * 同时匹配主名 + 别名 + description，取三者最高分。
 */
export function fuzzyFilterCommands(query: string, limit = 8): ScoredSlashCommand[] {
  const cmds = listLocalSlashCommands()

  if (!query) {
    return cmds.map(cmd => ({ cmd, score: 100, matchedKey: cmd.name }))
  }

  const scored: ScoredSlashCommand[] = []

  for (const cmd of cmds) {
    let best: { score: number; key: string } | null = null

    for (const key of [cmd.name, ...cmd.aliases]) {
      const s = fuzzyScore(query, key)

      if (s > 0 && (!best || s > best.score)) {
        best = { score: s, key }
      }
    }

    // description 也参与（弱权重）
    const descScore = fuzzyScore(query, cmd.description) * 0.5

    if (descScore > 0 && (!best || descScore > best.score)) {
      best = { score: descScore, key: cmd.description }
    }

    if (best) {
      scored.push({ cmd, score: best.score, matchedKey: best.key })
    }
  }

  scored.sort((a, b) => b.score - a.score || a.cmd.name.localeCompare(b.cmd.name))

  return scored.slice(0, limit)
}

export interface SlashCommandListResponse {
  commands: readonly ServerCommandEntry[]
}

/** gateway 打开后从服务端拉取命令元数据并写入 atom；幂等（已加载则跳过）。 */
export async function fetchSlashCommandMeta(
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
): Promise<void> {
  if ($slashCommandMeta.get().length > 0) {
    return
  }

  try {
    const res = await request<SlashCommandListResponse>('command.list', {})
    setSlashCommandMeta((res.commands ?? []).map(normalizeServerEntry))
  } catch {
    // 失败时保持空数组：用户敲 / 不会弹层，但 prompt.submit 仍正常。
  }
}
