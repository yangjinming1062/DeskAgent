/** Shared types for the 3D engine layer. */

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