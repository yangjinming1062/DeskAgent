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
}

/** Strip leading namespace prefix from bone name. */
export function boneSuffix(name: string): string {
  const sep = name.indexOf(':')

  return sep >= 0 ? name.slice(sep + 1) : name
}
