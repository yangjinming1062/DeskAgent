export interface EngineOptions {
  canvas: HTMLCanvasElement
  width: number
  height: number
}

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
