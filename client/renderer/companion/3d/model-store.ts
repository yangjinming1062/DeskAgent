import { atom, computed } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { $gateway } from '@/shared/store/gateway'

import type { ClipDef } from './clips-biped'

// 3D 伙伴的模型与衣橱资产目录。
// 后端接口：/api/companion/model 与 /api/companion/wardrobe；
// 通过网关推送 model.ready / wardrobe.updated 事件，并在 lifecycle=ready 时
// 由 ``hydrateModel`` / ``hydrateWardrobe`` 拉取一次。

export interface ModelInfo {
  id: number | null
  asset_url: string | null
  species: string | null
  provider: string | null
  morph_params: Record<string, number>
  has_rig: boolean
  has_morph_targets: boolean
  status: string
  rig_type: string
  rig_naming: string
  content_hash: string | null
}

export interface WardrobeItem {
  id: number
  name: string
  category: string
  // 后端返回的原始 JSON 字符串 —— 应用材质覆盖前需先解析。
  material_overrides_json: string
  texture_url: string | null
  // 与 `texture_url`（albedo）配套的 PBR 通道，均可空。
  normal_url?: string | null
  roughness_url?: string | null
  metalness_url?: string | null
  displacement_url?: string | null
  prompt?: string | null
  outfit_description?: string | null
  equipped: boolean
  origin?: string
  gift_state?: string | null
  gift_reason?: string | null
  gift_message?: string | null
  // 几何衣橱（PROTOCOL.md §1.6）。``slot`` 是后端解析出的互斥槽位；
  // 客户端构建的预览候选上没有该字段。
  kind?: string
  mesh_url?: string | null
  assembly_json?: string
  slot?: string
}

export interface CompanionExpression {
  id: number
  name: string
  label: string
  valence: string
  description: string
  icon?: string | null
  tags: string[]
}

interface CompanionModelResponse {
  id: number
  asset_url: string | null
  provider: string
  species: string
  morph_params: Record<string, number>
  status: string
  rig_type: string
  rig_naming: string
  has_rig: boolean
  has_morph_targets: boolean
  content_hash?: string | null
}

export const $modelInfo = atom<ModelInfo>({
  id: null,
  asset_url: null,
  species: null,
  provider: null,
  morph_params: {},
  has_rig: false,
  has_morph_targets: false,
  status: 'pending',
  rig_type: 'biped',
  rig_naming: 'mixamo',
  content_hash: null
})

export const $wardrobe = atom<WardrobeItem[]>([])
// 首次 loadCharacter() 完成（GLB 解析成功或回退到程序化模型）后置为 true。
// 用于门控渲染功率调度器：在它变 true 之前，孵化阶段全速运行，
// 不受 idle/sleep 信号影响。
export const $modelLoadSettled = atom<boolean>(false)
// 多装备：每个槽位（outfit / torso / legs / feet / head / …）同时只能装一个；
// 数组保存当前全套已装备项。
export const $equippedItems = atom<WardrobeItem[]>([])
export const $availableClipNames = atom<Set<string>>(new Set())
export const $generatedClips = atom<ClipDef[]>([])

/** 根据 item 或 assembly_json 解析互斥槽位。 */
export function slotOf(item: WardrobeItem): string {
  if (item.slot) {
    return item.slot
  }

  if ((item.kind ?? 'texture') === 'texture' || !item.mesh_url) {
    return 'outfit'
  }

  const asm = safeJsonParse<unknown>(item.assembly_json ?? '{}', {})

  const slot = asm && typeof asm === 'object' && !Array.isArray(asm) ? (asm as { slot?: unknown }).slot : null

  return typeof slot === 'string' && slot ? slot : 'torso'
}

// ── 换装候选回溯（镜像 $portraitHistory）──
export interface WardrobeCandidate {
  url: string
  prompt: string
  fileId: string
  description: string
  normalUrl?: string
  normalFileId?: string
  roughnessUrl?: string
  roughnessFileId?: string
  metalnessUrl?: string
  metalnessFileId?: string
  displacementUrl?: string
  displacementFileId?: string
  // 几何衣橱（PROTOCOL.md §1.6）。
  meshUrl?: string
  meshFileId?: string
  kind?: string
  assemblyJson?: string
}

const _MAX_CANDIDATES = 3

export const $wardrobeCandidates = atom<WardrobeCandidate[]>([])
export const $wardrobeSelectedIdx = atom<number>(0)
// 选中候选时设为临时 outfit spec；为 null 时回退到 $equippedItems。
export const $wardrobePreview = atom<WardrobeItem | null>(null)

// 构造预览期间 CharacterController.setOutfit 使用的临时 WardrobeItem。
// 集中在这里，让 id:-1 这个哨兵值只出现在一处。
function _candidateToPreview(c: WardrobeCandidate): WardrobeItem {
  return {
    id: -1,
    name: 'preview',
    category: 'draft',
    material_overrides_json: '{}',
    texture_url: c.url,
    normal_url: c.normalUrl ?? null,
    roughness_url: c.roughnessUrl ?? null,
    metalness_url: c.metalnessUrl ?? null,
    displacement_url: c.displacementUrl ?? null,
    prompt: c.prompt,
    equipped: false,
    kind: c.kind ?? 'texture',
    mesh_url: c.meshUrl ?? null,
    assembly_json: c.assemblyJson ?? '{}'
  }
}

export function pushWardrobeCandidate(c: WardrobeCandidate): void {
  const current = $wardrobeCandidates.get()
  const next = [...current, c]

  if (next.length > _MAX_CANDIDATES) {
    next.shift()
  }

  $wardrobeCandidates.set(next)
  $wardrobeSelectedIdx.set(next.length - 1)
  $wardrobePreview.set(_candidateToPreview(c))
}

export function selectWardrobeCandidate(idx: number): void {
  const current = $wardrobeCandidates.get()

  if (idx >= 0 && idx < current.length) {
    $wardrobeSelectedIdx.set(idx)
    $wardrobePreview.set(_candidateToPreview(current[idx]))
  }
}

export function clearWardrobeCandidates(): void {
  $wardrobeCandidates.set([])
  $wardrobeSelectedIdx.set(0)
  $wardrobePreview.set(null)
}

// ── Generation progress tracking ──
// model.gen.progress → $modelGenState='generating' + $modelGenProgress
// model.ready        → $modelGenState='succeeded'
// model.failed       → $modelGenState='failed' + $modelGenError
// model.failed 且带 retry_download → 付费结果仍留在后端，只是下载失败；
// $modelRetryable 门控"重试下载"动作（companion.model.retryDownload —— 绝不重新计费生成）。
export type ModelGenState = 'idle' | 'generating' | 'succeeded' | 'failed'
export const $modelGenState = atom<ModelGenState>('idle')
export const $modelGenProgress = atom<{ stage: string; progress: number } | null>(null)
export const $modelGenError = atom<string | null>(null)
export const $modelRetryable = atom<boolean>(false)
export const $modelRetryModelId = atom<number | null>(null)

export function setModelFailed(reason: string, opts: { retryDownload?: boolean; modelId?: number | null } = {}): void {
  $modelGenState.set('failed')
  $modelGenError.set(reason)
  $modelGenProgress.set(null)
  $modelRetryable.set(opts.retryDownload === true)
  $modelRetryModelId.set(opts.modelId ?? null)
}

export function clearModelRetry(): void {
  $modelRetryable.set(false)
  $modelRetryModelId.set(null)
}

/** companion.model.retryDownload —— 重放一次已经付费生成结果的下载
 * （后端仅刷新 URL 签名查询，不会重新提交生成）。进度沿同一条 model.* 事件流回传。 */
export async function retryModelDownload(modelId: number): Promise<void> {
  const gateway = $gateway.get()

  if (!gateway) {
    log.warn('model-store', 'retryModelDownload: gateway not ready')

    return
  }

  try {
    await gateway.request('companion.model.retryDownload', { model_id: modelId })
    $modelGenState.set('generating')
    $modelGenProgress.set({ stage: 'downloading', progress: 88 })
    $modelGenError.set(null)
    clearModelRetry()
  } catch (err) {
    log.warn('model-store', 'retryModelDownload failed', err)
  }
}

export function setModelInfo(next: Partial<ModelInfo>): void {
  $modelInfo.set({ ...$modelInfo.get(), ...next })
}

/** 渲染集合：已装备项按槽位叠加当前预览候选。 */
export const $outfitView = computed([$equippedItems, $wardrobePreview], (equipped, preview) =>
  preview ? equipped.filter(i => slotOf(i) !== slotOf(preview)).concat(preview) : equipped
)

export function setWardrobe(items: WardrobeItem[]): void {
  $wardrobe.set(items)
  $equippedItems.set(items.filter(i => i.equipped))
}

export function refreshEquippedAndApply(): WardrobeItem[] {
  const equipped = $wardrobe.get().filter(i => i.equipped)
  $equippedItems.set(equipped)

  return equipped
}

// 在 lifecycle=ready 时从后端拉取当前模型。后端每次调用都会重签 ``asset_url``（5 分钟 TTL），
// 不做本地缓存。404（onboarding 期间还没有模型）静默吞掉，让初始原子值保持默认；
// 5xx / 网络错误则打 warn，避免生产环境模型缺失悄无声息。
export async function ensureModelGeneration(): Promise<void> {
  try {
    const res = await window.spiritagent.api<{ id?: number; status?: string }>({
      path: '/api/companion/model',
      method: 'POST',
      body: {}
    })

    // 后端返回的是一行下载失败的记录（而非重新生成）—— 付费结果仍可恢复，
    // 因此弹出重试动作，而不是让 failed 状态暗示"必须重新生成"。
    if (res?.status === 'download_failed') {
      setModelFailed('3D 模型下载失败，可重试下载', { retryDownload: true, modelId: res.id ?? null })
    }
  } catch (err) {
    log.info('model-store', 'ensureModelGeneration requested:', err)
  }
}

export async function hydrateModel(): Promise<void> {
  try {
    const res = await window.spiritagent.api<CompanionModelResponse>({
      path: '/api/companion/model'
    })

    if (res && res.asset_url && res.status === 'succeeded') {
      setModelInfo({
        id: res.id,
        asset_url: res.asset_url,
        species: res.species,
        provider: res.provider,
        morph_params: res.morph_params ?? {},
        has_rig: res.has_rig,
        has_morph_targets: res.has_morph_targets,
        status: res.status,
        rig_type: res.rig_type ?? 'biped',
        rig_naming: res.rig_naming ?? 'mixamo',
        content_hash: res.content_hash ?? null
      })

      return
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', 'hydrateModel failed', error)
    }
  }

  // lifecycle=ready 阶段还没有可用模型时，自动启动生成
  void ensureModelGeneration()
}

// 与 ``hydrateModel`` 形态一致 —— GET /api/companion/wardrobe 后写入
// ``$wardrobe``（同时衍生 ``$equippedItems``）。在 lifecycle=ready 时的水合
// 与 ``wardrobe.updated`` WS 事件处理之间共用。
export async function hydrateWardrobe(): Promise<void> {
  try {
    const res = await window.spiritagent.api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
    setWardrobe(res ?? [])
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', 'hydrateWardrobe failed', error)
    }
  }
}

export const $expressions = atom<CompanionExpression[]>([])

/** GET → 原子的水合函数。静默吞掉 4xx（前置条件不满足）让原子保持默认值；
 * 5xx / 网络错误则打 warn，避免生产环境资产缺失悄无声息。 */
async function hydrateArray<T>(
  path: string,
  atom: { set(value: T[]): void },
  arrayKey: string,
  label: string
): Promise<void> {
  try {
    const res = await window.spiritagent.api<Record<string, unknown>>({ path })
    const arr = res?.[arrayKey]

    if (Array.isArray(arr)) {
      atom.set(arr as T[])
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', `${label} failed`, error)
    }
  }
}

export async function hydrateGeneratedClips(): Promise<void> {
  await hydrateArray<ClipDef>('/api/companion/animations', $generatedClips, 'clips', 'hydrateGeneratedClips')
}

export async function hydrateExpressions(): Promise<void> {
  await hydrateArray<CompanionExpression>(
    '/api/companion/expressions',
    $expressions,
    'expressions',
    'hydrateExpressions'
  )
}
