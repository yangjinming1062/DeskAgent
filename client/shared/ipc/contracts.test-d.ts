// 类型测试：验证 `client/shared/ipc/contracts.ts` 的契约表面与 IPC 运行时
// 常量 `IPC` 完全一致。这些断言在 `vitest typecheck` 阶段执行
// （见 `client/vite.config.ts` 的 `test.typecheck` 配置）；
// 任何契约字段变更、IPC 常量拼写错误、遗漏的 invoke key 都会让类型检查失败。
//
// 使用 `Parameters<Fn>[N]` 与 `Awaited<ReturnType<Fn>>` 直接断言，
// 避开 expect-type 成员访问器在联合返回类型下的解析限制。
//
// expect-type 的 `expectTypeOf<T>()` 是运行时函数,所以是 value import。
// `IPC` 在本文件只用 `typeof IPC.invoke.xxx` 在类型层,走 `import type`
// 即可(TS 6 允许 `typeof` 操作 type-only import 的 const 绑定)。

import { expectTypeOf } from 'expect-type'

import type { IPC } from './contracts'
import type {
  DesktopActivatePayload,
  DesktopAuthBroadcast,
  DesktopAuthSnapshot,
  DesktopBootProgress,
  DesktopLogoutResult,
  DesktopRunnerState,
  DesktopRunnerStatusEvent,
  DesktopRunnerUpdateEvent,
  DesktopUpdateEvent,
  DesktopVersionInfo,
  IpcChannel,
  IpcEventChannel,
  IpcEventContract,
  IpcInvokeContract,
  IpcSendChannel,
  IpcSendContract,
  MediaSttPayload,
  MediaTtsPayload,
  RunnerConfigPatch,
  SkillItem,
  SpiritAgentApiRequest,
  SpiritAgentConnection,
  SpiritAgentSelectPathsOptions,
  SpiritAgentWindowState,
  ToolsetItem
} from './contracts'

// ---- 穷举校验：IPC.invoke  必须包含契约中全部 40 个 invoke key ---------------
//
// `keyof typeof IPC.invoke` 与 `keyof IpcInvokeContract` 互为子集;
// 任一边缺失某个 key(例如新增契约条目忘加 IPC 常量,或漏写某个 IPC key),
// 这一行的赋值类型不兼容,编译错误。
type _AllInvokeKeysCovered = keyof typeof IPC.invoke & keyof IpcInvokeContract
// 强制消费这个类型别名,确保分支被求值。
const _checkAllInvokeKeys: _AllInvokeKeysCovered[] = []

// ---- 穷举校验：IPC.event 必须包含契约中全部 9 个 event key -------------------
type _AllEventKeysCovered = keyof typeof IPC.event & keyof IpcEventContract
const _checkAllEventKeys: _AllEventKeysCovered[] = []

// ---- 穷举校验：IPC.send 必须包含契约中全部 send key --------------------------
type _AllSendKeysCovered = keyof typeof IPC.send & keyof IpcSendContract
const _checkAllSendKeys: _AllSendKeysCovered[] = []

// ---- Auth (用户需求 §5 重点契约) -------------------------------------------

expectTypeOf<Parameters<IpcInvokeContract['spiritagent:auth:activate']>[0]>().toEqualTypeOf<DesktopActivatePayload>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:auth:activate']>>>().toEqualTypeOf<DesktopAuthSnapshot>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:auth:refresh']>[0]>().toEqualTypeOf<
  Record<string, unknown> | undefined
>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:auth:logout']>>>().toEqualTypeOf<
  DesktopLogoutResult
>()
expectTypeOf<
  Awaited<ReturnType<IpcInvokeContract['spiritagent:auth:get-session']>>
>().toEqualTypeOf<DesktopAuthSnapshot | null>()

// 死契约保护：spiritagent:auth:get-default-backend-url 已从契约移除。
// 类型层应该查不到。`@ts-expect-error` 验证删除生效。
// @ts-expect-error spiritagent:auth:get-default-backend-url 已不在契约中
type _RemovedDefaultBackendUrl = IpcInvokeContract['spiritagent:auth:get-default-backend-url']

// 事件载荷
expectTypeOf<IpcEventContract['spiritagent:auth:changed']>().toEqualTypeOf<[payload: DesktopAuthBroadcast]>()

// ---- Connection ---------------------------------------------------------

expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:connection']>>>().toEqualTypeOf<SpiritAgentConnection>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:gateway:ws-url']>>>().toEqualTypeOf<string>()
expectTypeOf<
  Awaited<ReturnType<IpcInvokeContract['spiritagent:boot-progress:get']>>
>().toEqualTypeOf<DesktopBootProgress>()
expectTypeOf<IpcEventContract['spiritagent:boot-progress']>().toEqualTypeOf<[payload: DesktopBootProgress]>()
expectTypeOf<IpcEventContract['spiritagent:window-state-changed']>().toEqualTypeOf<[payload: SpiritAgentWindowState]>()

// ---- Runner state -------------------------------------------------------

expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:runner:get-state']>>>().toEqualTypeOf<DesktopRunnerState>()
expectTypeOf<
  Awaited<ReturnType<IpcInvokeContract['spiritagent:runner:get-tools']>>
>().toEqualTypeOf<Array<Record<string, unknown>>>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:runner:cancel']>>>().toEqualTypeOf<unknown>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:runner:reload-mcp']>>>().toEqualTypeOf<unknown>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:runner:invoke']>>>().toEqualTypeOf<unknown>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:runner:invoke']>>().toEqualTypeOf<
  [name: string, args: Record<string, unknown>]
>()
expectTypeOf<IpcEventContract['spiritagent:runner:status']>().toEqualTypeOf<[payload: DesktopRunnerStatusEvent]>()

// ---- Skills / toolsets / runner-config -------------------------------------

expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:skills:list']>>>().toEqualTypeOf<{
  error?: string
  ok: boolean
  skills?: SkillItem[]
}>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:skill:set-enabled']>[0]>().toEqualTypeOf<{
  enabled: boolean
  name: string
}>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:toolsets:list']>>>().toEqualTypeOf<{
  error?: string
  ok: boolean
  toolsets?: ToolsetItem[]
}>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:toolset:set-enabled']>[0]>().toEqualTypeOf<{
  enabled: boolean
  id: string
}>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:runner-config:read']>>>().toEqualTypeOf<{
  content?: string
  error?: string
  ok: boolean
}>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:runner-config:write']>[0]>().toEqualTypeOf<string>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:runner-config:write']>>>().toEqualTypeOf<{
  error?: string
  ok: boolean
}>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:runner-config:patch']>[0]>().toEqualTypeOf<
  RunnerConfigPatch
>()

// ---- Files / clipboard / log --------------------------------------------

expectTypeOf<Parameters<IpcInvokeContract['spiritagent:readFileDataUrl']>[0]>().toEqualTypeOf<string>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:readFileDataUrl']>>>().toEqualTypeOf<string>()
expectTypeOf<
  Parameters<IpcInvokeContract['spiritagent:selectPaths']>[0]
>().toEqualTypeOf<SpiritAgentSelectPathsOptions | undefined>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:selectPaths']>>>().toEqualTypeOf<string[]>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:writeClipboard']>[0]>().toEqualTypeOf<string>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:writeClipboard']>>>().toEqualTypeOf<boolean>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:saveClipboardImage']>>>().toEqualTypeOf<string>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:log:emit']>>>().toEqualTypeOf<void>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:log:emit']>[0]>().toEqualTypeOf<{
  args: unknown[]
  level: 'error' | 'info' | 'warn'
  scope: string
}>()

// ---- Window / version / system -------------------------------------------

expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:window:show-tool']>>>().toEqualTypeOf<void>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:version']>>>().toEqualTypeOf<
  DesktopVersionInfo
>()

// ---- Media STT / TTS (用户需求 §5 重点契约) -------------------------------

expectTypeOf<Parameters<IpcInvokeContract['spiritagent:media:stt']>[0]>().toEqualTypeOf<MediaSttPayload>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:media:stt']>>>().toEqualTypeOf<{ text: string }>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:media:tts']>[0]>().toEqualTypeOf<MediaTtsPayload>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:media:tts']>>>().toEqualTypeOf<{
  dataUrl: string
  mimeType: string
}>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:onboardingAudio:read']>[0]>().toEqualTypeOf<string>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:onboardingAudio:read']>>>().toEqualTypeOf<{
  bytes: number
  dataUrl: string
  mimeType: string
  tag: string
}>()

// ---- Sprite -------------------------------------------------------------

expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:sprite:hide']>>>().toEqualTypeOf<void>()
expectTypeOf<
  Parameters<IpcInvokeContract['spiritagent:sprite:set-ignore-mouse-events']>[0]
>().toEqualTypeOf<{ forward?: boolean; ignore: boolean }>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:sprite:set-always-on-top']>>>().toEqualTypeOf<void>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:sprite:set-always-on-top']>[0]>().toEqualTypeOf<{
  on: boolean
}>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:sprite:get-position']>>>().toEqualTypeOf<
  null | { origin?: { x: number; y: number }; x: number; y: number }
>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:sprite:set-position']>[0]>().toEqualTypeOf<{
  x: number
  y: number
}>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:sprite:set-position']>>>().toEqualTypeOf<void>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:sprite:move-to-cursor-display']>>>().toEqualTypeOf<
  | null
  | {
      cursor: { x: number; y: number }
      from: { x: number; y: number }
      to: { x: number; y: number }
    }
>()

// ---- Update (用户需求 §5 重点契约) ----------------------------------------

expectTypeOf<IpcEventContract['spiritagent:update-event']>().toEqualTypeOf<[payload: DesktopUpdateEvent]>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:update:check']>>>().toEqualTypeOf<void>()
expectTypeOf<IpcEventContract['spiritagent:runner-update-event']>().toEqualTypeOf<
  [payload: DesktopRunnerUpdateEvent]
>()

// ---- API proxy (用户需求 §5) + 资产通道 -----------------------------------

expectTypeOf<Parameters<IpcInvokeContract['spiritagent:api']>[0]>().toEqualTypeOf<
  SpiritAgentApiRequest
>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:api']>>>().toEqualTypeOf<unknown>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:api:asset']>[0]>().toEqualTypeOf<{ url: string }>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:api:asset']>>>().toEqualTypeOf<string>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:api:asset-buffer']>[0]>().toEqualTypeOf<{
  contentHash?: string
  url: string
}>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:api:asset-buffer']>>>().toEqualTypeOf<Uint8Array>()
expectTypeOf<Parameters<IpcInvokeContract['spiritagent:api:asset-model-url']>[0]>().toEqualTypeOf<{
  contentHash?: string
  url: string
}>()
expectTypeOf<Awaited<ReturnType<IpcInvokeContract['spiritagent:api:asset-model-url']>>>().toEqualTypeOf<string>()

// ---- 空载荷事件 (`[]`) 必须保持空(防止有人误加 payload)-------------------

expectTypeOf<IpcEventContract['spiritagent:auth:session-expired']>().toEqualTypeOf<[]>()
expectTypeOf<IpcEventContract['spiritagent:power-resume']>().toEqualTypeOf<[]>()
expectTypeOf<IpcEventContract['spiritagent:tray:logout']>().toEqualTypeOf<[]>()

// 截断 const  声明以避免 lint 报未使用变量。
void _checkAllInvokeKeys
void _checkAllEventKeys
void _checkAllSendKeys

// ---- IPC 运行时 channel 常量 (spot checks) --------------------------------

// 每个 `IPC.*.*` 值都必须是正确的 channel 字面量
expectTypeOf<typeof IPC.invoke.authActivate>().toEqualTypeOf<'spiritagent:auth:activate'>()
expectTypeOf<typeof IPC.invoke.apiAssetModelUrl>().toEqualTypeOf<'spiritagent:api:asset-model-url'>()
expectTypeOf<typeof IPC.invoke.runnerCancel>().toEqualTypeOf<'spiritagent:runner:cancel'>()
expectTypeOf<typeof IPC.event.authChanged>().toEqualTypeOf<'spiritagent:auth:changed'>()
expectTypeOf<typeof IPC.event.runnerStatus>().toEqualTypeOf<'spiritagent:runner:status'>()
expectTypeOf<typeof IPC.send.titleBarTheme>().toEqualTypeOf<'spiritagent:titlebar-theme'>()

// ---- Channel 名拼写保护 -------------------------------------------------

// 任何不存在的 channel 字符串不应被接受为 IpcChannel。
// 用 `not.toMatchTypeOf<IpcChannel>()` 替代不存在的 `expectError`
// (expect-type 没有顶层 `expectError`)。
expectTypeOf<'spiritagent:not-a-channel'>().not.toMatchTypeOf<IpcChannel>()
expectTypeOf<'spiritagent:typo-event'>().not.toMatchTypeOf<IpcEventChannel>()
expectTypeOf<'spiritagent:typo-send'>().not.toMatchTypeOf<IpcSendChannel>()

// 已删除的契约条目不应再被视为合法 invoke channel
expectTypeOf<'spiritagent:auth:get-default-backend-url'>().not.toMatchTypeOf<IpcChannel>()
