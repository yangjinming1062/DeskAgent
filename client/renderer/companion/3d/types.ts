export interface EngineOptions {
  container?: HTMLElement
  /** Whether the renderer should enable shadow mapping. Default `false` — for a 300×360 desktop-pet window PBR environment lighting alone conveys depth, and a 2048² PCFSoft shadow map is the single biggest GPU cost in the pipeline. When enabled, capped at 1024² PCF. */
  useShadows?: boolean
}

export type EngineBackendKind = 'webgpu' | 'webgl2' | 'classic-webgl'

export interface LoadedModelInfo {
  hasMorphTargets: boolean
  hasAnimations: boolean
  clipNames: string[]
  morphNames: string[]
  /** True when load() fell through to the procedural egg (no bytes / parse failed). */
  procedural: boolean
}

/** Strip leading namespace prefix from bone name. */
export function boneSuffix(name: string): string {
  const sep = name.indexOf(':')

  return sep >= 0 ? name.slice(sep + 1) : name
}
