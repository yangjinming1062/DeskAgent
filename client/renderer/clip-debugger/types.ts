import type * as THREE from 'three'

import type { RigType } from '@/companion/3d/rig'

export type { RigType }

type ClipCategory = 'preset' | 'embedded'

export interface ClipItem {
  id: string
  name: string
  duration: number
  loop: boolean
  category: ClipCategory
  tags?: readonly string[]
  trackCount: number
  animationClip?: THREE.AnimationClip
}

export type PlaybackLoopMode = 'default' | 'force-loop' | 'force-once'

export interface PlaybackState {
  isPlaying: boolean
  currentTime: number
  duration: number
  speed: number
  loopMode: PlaybackLoopMode
  crossFadeDuration: number
}

export type ViewportBackground = 'studio' | 'slate' | 'transparent' | 'light' | 'midnight'

export interface ViewportOptions {
  showSkeleton: boolean
  /** 实体骨段绘制（锥形骨头，叠加于模型之上） */
  showBones: boolean
  /** 关节球绘制 */
  showJoints: boolean
  /** 模型半透明全息风格，便于透视观察内部骨骼 */
  showHologram: boolean
  showGrid: boolean
  showAxes: boolean
  showWireframe: boolean
  background: ViewportBackground
  lightIntensity: number
  autoRotate: boolean
  cameraFov: number
}

export interface MorphTargetInfo {
  name: string
  index: number
  meshName: string
  currentValue: number
}

export interface ModelStats {
  sourceType: 'mannequin' | 'custom-glb' | 'procedural-egg'
  name: string
  fileSizeBytes?: number
  vertexCount: number
  triangleCount: number
  meshCount: number
  boneCount: number
  hasMorphs: boolean
  hasEmbeddedAnimations: boolean
}

export type TransformMode = 'view' | 'translate' | 'rotate' | 'scale'

export interface ModelTransform {
  position: { x: number; y: number; z: number }
  rotation: { x: number; y: number; z: number } // 角度 (degrees)
  scale: number
}
