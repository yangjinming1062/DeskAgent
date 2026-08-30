import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { definePersistedAtom, definePersistedEnum } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

// 2D 命中检测的引用句柄（懒加载；SpriteStage 在 pointermove / tap 时通过
// 这里的 ref 调用 hitRegion，避免 React 重渲染）。
// 注意：Mesh2DHitmap 内部只用归一化坐标，nx / ny 必须由调用方先换算。
export const $mesh2dHitmap = atom<{ hit: (nx: number, ny: number) => { region: string } | null } | null>(null)

export function setMesh2DHitmap(map: { hit: (nx: number, ny: number) => { region: string } | null } | null): void {
  $mesh2dHitmap.set(map)
}

export type Mesh2DStatus = 'idle' | 'generating' | 'succeeded' | 'failed'
export type RenderMode = '2d' | '3d'

interface Mesh2DInfo {
  id: number | null
  status: Mesh2DStatus
  style: string
  manifestUrl: string | null
  layerUrls: Record<string, string>
  contentHash: string | null
  error: string | null
}

interface Mesh2DResponse {
  id?: number
  status?: string
  style?: string
  manifest_url?: string | null
  layer_urls?: Record<string, string>
  content_hash?: string | null
  error?: string | null
}

const renderModePersisted = definePersistedEnum<RenderMode>({
  allowed: ['2d', '3d'] as const,
  fallback: '2d',
  key: 'da.companion.renderMode'
})

export const $renderMode = renderModePersisted.$atom
export const setRenderMode = renderModePersisted.set

const DEFAULT_MESH2D_INFO: Mesh2DInfo = {
  contentHash: null,
  error: null,
  id: null,
  layerUrls: {},
  manifestUrl: null,
  status: 'idle',
  style: 'cel_shading'
}

function isPersistableMesh2D(val: unknown): val is Mesh2DInfo {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const v = val as Partial<Mesh2DInfo>

  return (
    v.status === 'succeeded' &&
    typeof v.manifestUrl === 'string' &&
    Boolean(v.manifestUrl) &&
    typeof v.layerUrls === 'object' &&
    v.layerUrls !== null
  )
}

const mesh2dInfoPersisted = definePersistedAtom<Mesh2DInfo>({
  fallback: DEFAULT_MESH2D_INFO,
  isPersistable: isPersistableMesh2D,
  key: 'da.companion.mesh2d'
})

export const $mesh2dInfo = mesh2dInfoPersisted.$atom
export const resetMesh2D = mesh2dInfoPersisted.reset

export function setMesh2DInfo(next: Partial<Mesh2DInfo>): void {
  mesh2dInfoPersisted.set(next)
}

export function setMesh2DStatus(status: Mesh2DStatus, error?: string | null): void {
  setMesh2DInfo({ error: error ?? null, status })
}

/** 拉取当前激活 2D 模型状态：服务端推送的 mesh2d 行（manifest 仍由 hydratePuppet 异步分流）。 */
export async function hydrateMesh2D(): Promise<void> {
  const result = await authedApi<Mesh2DResponse>({ path: '/api/companion/2d' })

  if (!result.ok) {
    if (result.reason === 'err') {
      log.info('mesh2d-store', 'hydrateMesh2D failed', result.error)
    }

    return
  }

  const mesh2d = result.value

  if (!mesh2d) {
    return
  }

  // 维持 succeeded 不变量：必须有 manifestUrl，否则留 'generating' 给下一轮事件回填
  // 避免下游 cascade 看到 status='succeeded' 但 manifest 为空而漏下文件。
  if (mesh2d.status === 'succeeded' && mesh2d.manifest_url) {
    setMesh2DInfo({
      contentHash: mesh2d.content_hash ?? null,
      error: null,
      id: mesh2d.id ?? null,
      layerUrls: mesh2d.layer_urls ?? {},
      manifestUrl: mesh2d.manifest_url,
      status: 'succeeded',
      style: mesh2d.style || 'cel_shading'
    })
  } else if (mesh2d.status === 'generating') {
    setMesh2DInfo({
      contentHash: null,
      error: null,
      id: mesh2d.id ?? null,
      layerUrls: {},
      manifestUrl: null,
      status: 'generating',
      style: mesh2d.style || 'cel_shading'
    })
  } else if (mesh2d.status === 'failed') {
    setMesh2DInfo({
      contentHash: null,
      error: mesh2d.error ?? '2D 形象生成失败',
      id: mesh2d.id ?? null,
      layerUrls: {},
      manifestUrl: null,
      status: 'failed',
      style: mesh2d.style || 'cel_shading'
    })
  }
}

/** 触发（或重试）2D 拆分（DESIGN §5.5：失败后由用户在设置中重试）。
 *  就绪结果经 companion.2d.ready 事件回流，与 onboarding 确认路径同一条管线。 */
export async function requestMesh2DGeneration(): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  setMesh2DStatus('generating')

  const result = await authedApi({ method: 'POST', path: '/api/companion/2d' })

  if (result.ok) {
    // void body（204 / null）即后端已受理；就绪结果走 companion.2d.ready 事件回流。
    // 不再以 res===null && auth==='authenticated' 当作失败处理——避免 spinner 卡死。
    return
  }

  if (result.reason === 'unauth') {
    setMesh2DStatus('idle')

    return
  }

  log.warn('mesh2d-store', 'requestMesh2DGeneration failed', result.error)
  setMesh2DStatus('failed', '切分请求失败，请稍后再试')
}

/** 切换渲染模式：切到 3D 时由后端触发 3D 生成；切到 2D 立即生效。 */
export async function switchRenderMode(mode: RenderMode): Promise<void> {
  const previous = $renderMode.get()

  // 幂等守卫：render_mode.changed 广播回流时会带着当前值再调一次，
  // 无守卫会重复 POST 并再次触发后端广播形成回环。
  if (mode === previous) {
    return
  }

  setRenderMode(mode)

  const result = await authedApi<{ render_mode?: string }>({
    body: { render_mode: mode },
    method: 'POST',
    path: '/api/companion/render-mode'
  })

  if (result.ok) {
    if (mode === '2d') {
      await hydrateMesh2D()
    }

    return
  }

  // 仅在后端真正报错时回滚；auth-loss 是用户主动登出，回滚无意义且会污染 UI。
  if (result.reason === 'err') {
    log.warn('mesh2d-store', 'switchRenderMode failed; rolling back', result.error)
    setRenderMode(previous)
  }
}
