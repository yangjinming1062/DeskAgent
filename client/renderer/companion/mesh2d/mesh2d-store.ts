import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

export type Mesh2DStatus = 'idle' | 'generating' | 'succeeded' | 'failed'
export type RenderMode = '2d' | '3d'

export interface Mesh2DInfo {
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

export const $mesh2dInfo = atom<Mesh2DInfo>({
  id: null,
  status: 'idle',
  style: 'cel_shading',
  manifestUrl: null,
  layerUrls: {},
  contentHash: null,
  error: null
})

// manifest 与 layers 已就位。Mesh2DCanvas 在它变 true 后才进入构建循环。
export const $mesh2dReady = atom<boolean>(false)

// 用户在 onboarding / 设置里选的渲染模式。默认 '2d'。
export const $renderMode = atom<RenderMode>('2d')

export function setRenderMode(mode: RenderMode): void {
  $renderMode.set(mode)
}

export function setMesh2DInfo(next: Partial<Mesh2DInfo>): void {
  $mesh2dInfo.set({ ...$mesh2dInfo.get(), ...next })
}

export function setMesh2DStatus(status: Mesh2DStatus, error?: string | null): void {
  setMesh2DInfo({ status, error: error ?? null })
}

export function resetMesh2D(): void {
  $mesh2dInfo.set({
    id: null,
    status: 'idle',
    style: 'cel_shading',
    manifestUrl: null,
    layerUrls: {},
    contentHash: null,
    error: null
  })
  $mesh2dReady.set(false)
}

/** 拉取当前激活 mesh2d 模型 + 用户 render_mode；lifecycle=ready 时调用一次。 */
export async function hydrateMesh2D(): Promise<void> {
  try {
    const mesh2d = await window.spiritagent.api<Mesh2DResponse>({ path: '/api/companion/mesh2d' })

    if (mesh2d && mesh2d.status === 'succeeded' && mesh2d.manifest_url) {
      $mesh2dInfo.set({
        id: mesh2d.id ?? null,
        status: 'succeeded',
        style: mesh2d.style || 'cel_shading',
        manifestUrl: mesh2d.manifest_url,
        layerUrls: mesh2d.layer_urls ?? {},
        contentHash: mesh2d.content_hash ?? null,
        error: null
      })
      $mesh2dReady.set(true)

      return
    }

    if (mesh2d && mesh2d.status === 'generating') {
      $mesh2dInfo.set({
        id: mesh2d.id ?? null,
        status: 'generating',
        style: mesh2d.style || 'cel_shading',
        manifestUrl: null,
        layerUrls: {},
        contentHash: null,
        error: null
      })
      $mesh2dReady.set(false)
    }
  } catch (err) {
    log.info('mesh2d-store', 'hydrateMesh2D failed', err)
  }

  try {
    const persona = await window.spiritagent.api<{ render_mode?: string }>({ path: '/api/companion/persona' })

    if (persona?.render_mode === '3d' || persona?.render_mode === '2d') {
      $renderMode.set(persona.render_mode)
    }
  } catch (err) {
    log.info('mesh2d-store', 'hydrate render mode failed', err)
  }
}

/** 切换渲染模式：切到 3D 时由后端触发 3D 生成；切到 2D 立即生效。 */
export async function switchRenderMode(mode: RenderMode): Promise<void> {
  const previous = $renderMode.get()

  setRenderMode(mode)

  try {
    await window.spiritagent.api<{ render_mode?: string }>({
      path: '/api/companion/render-mode',
      method: 'POST',
      body: { render_mode: mode }
    })

    if (mode === '2d') {
      $mesh2dReady.set(false)
      await hydrateMesh2D()
    }
  } catch (err) {
    log.warn('mesh2d-store', 'switchRenderMode failed; reverting', err)
    setRenderMode(previous)
  }
}
