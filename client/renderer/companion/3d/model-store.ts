import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { $gateway } from '@/shared/store/gateway'

// 3D 伙伴的模型与资产目录。
// 后端接口：/api/companion/model；
// 通过网关推送 model.ready 事件，并在 lifecycle=ready 时由 ``hydrateModel`` 拉取一次。

export interface ModelInfo {
  id: number | null
  asset_url: string | null
  species: string | null
  provider: string | null
  has_rig: boolean
  status: string
  rig_type: string
  rig_naming: string
  style: string
  content_hash: string | null
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
  status: string
  rig_type: string
  rig_naming: string
  has_rig: boolean
  content_hash?: string | null
  clip_map?: Readonly<Record<string, string>>
}

export const $modelInfo = atom<ModelInfo>({
  id: null,
  asset_url: null,
  species: null,
  provider: null,
  has_rig: false,
  status: 'pending',
  rig_type: 'biped',
  rig_naming: 'tripo',
  style: 'realistic',
  content_hash: null
})

// 首次 loadCharacter() 完成（GLB 解析成功或回退到程序化模型）后置为 true。
// 用于门控渲染功率调度器：在它变 true 之前，孵化阶段全速运行，
// 不受 idle/sleep 信号影响。
export const $modelLoadSettled = atom<boolean>(false)
export const $availableClipNames = atom<Set<string>>(new Set())
// 供应商声明的「语义键 → GLB 内 clip 名」；空表即该产物不含动画。
export const $clipMap = atom<Readonly<Record<string, string>>>({})

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
        has_rig: res.has_rig,
        status: res.status,
        rig_type: res.rig_type ?? 'biped',
        rig_naming: res.rig_naming ?? 'tripo',
        content_hash: res.content_hash ?? null
      })
      $clipMap.set(res.clip_map ?? {})

      return
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', 'hydrateModel failed', error)
    }
  }

  // hydrateModel 只读不写：3D 生成由 confirm-front 成功后显式触发，避免 onboarding 中段误启动
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

export async function hydrateExpressions(): Promise<void> {
  await hydrateArray<CompanionExpression>(
    '/api/companion/expressions',
    $expressions,
    'expressions',
    'hydrateExpressions'
  )
}
