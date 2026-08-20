import { atom, map } from 'nanostores'

import type { RigType } from '@/companion/3d/rig'

import type {
  ClipItem,
  ModelStats,
  ModelTransform,
  PlaybackLoopMode,
  PlaybackState,
  TransformMode,
  ViewportBackground,
  ViewportOptions
} from './types'

export const $selectedRig = atom<RigType>('biped')
export const $selectedCategory = atom<string>('all')
export const $searchQuery = atom<string>('')
export const $activeClip = atom<ClipItem | null>(null)
export const $scrubTime = atom<number | null>(null)
export const $stepFrameDelta = atom<number | null>(null)
export const $resetCameraSignal = atom<number>(0)
export const $autoGroundSignal = atom<number>(0)
export const $autoCenterSignal = atom<number>(0)
export const $normalizeHeightSignal = atom<number>(0)
export const $rotatePresetSignal = atom<{ axis: 'x' | 'y' | 'z'; deltaDeg: number } | null>(null)

export const $transformMode = atom<TransformMode>('view')
export const $modelTransform = map<ModelTransform>({
  position: { x: 0, y: 0, z: 0 },
  rotation: { x: 0, y: 0, z: 0 },
  scale: 1.0
})

export const $playbackState = map<PlaybackState>({
  isPlaying: true,
  currentTime: 0,
  duration: 4.0,
  speed: 1.0,
  loopMode: 'default',
  crossFadeDuration: 0.25
})

export const $viewportOptions = map<ViewportOptions>({
  showSkeleton: false,
  showBones: false,
  showJoints: false,
  showHologram: false,
  showGrid: true,
  showAxes: false,
  showWireframe: false,
  background: 'studio',
  lightIntensity: 1.2,
  autoRotate: false,
  cameraFov: 28
})

export const $morphWeights = map<Record<string, number>>({})
export const $lipSyncAmp = atom<number>(0)

export const $modelStats = atom<ModelStats | null>(null)
export const $customGlbBuffer = atom<{ buffer: ArrayBuffer; name: string } | null>(null)
export const $embeddedClips = atom<ClipItem[]>([])

export const RIG_LABELS: Record<RigType, { label: string; en: string; icon: string }> = {
  biped: { label: '人形双足', en: 'Biped', icon: '👤' },
  quadruped: { label: '四足动物', en: 'Quadruped', icon: '🐕' },
  avian: { label: '飞禽鸟类', en: 'Avian', icon: '🦅' },
  serpentine: { label: '蛇形游走', en: 'Serpentine', icon: '🐍' },
  aquatic: { label: '水生游禽', en: 'Aquatic', icon: '🐬' },
  hexapod: { label: '六足节肢', en: 'Hexapod', icon: '🕷️' },
  octopod: { label: '八足软体', en: 'Octopod', icon: '🐙' }
}

export const CATEGORY_LABELS: Record<string, string> = {
  all: '全部动作',
  preset: '预制动作',
  embedded: '模型内置'
}

export function selectClip(clip: ClipItem): void {
  $activeClip.set(clip)
  $playbackState.setKey('duration', clip.duration)
  $playbackState.setKey('currentTime', 0)
}

export function togglePlay(): void {
  const current = $playbackState.get().isPlaying
  $playbackState.setKey('isPlaying', !current)
}

export function setPlaybackSpeed(speed: number): void {
  $playbackState.setKey('speed', speed)
}

export function setLoopMode(mode: PlaybackLoopMode): void {
  $playbackState.setKey('loopMode', mode)
}

export function setCrossFadeDuration(seconds: number): void {
  $playbackState.setKey('crossFadeDuration', seconds)
}

export function triggerScrub(time: number): void {
  $scrubTime.set(time)
  $playbackState.setKey('currentTime', time)
}

export function stepFrame(deltaSeconds: number): void {
  $stepFrameDelta.set(deltaSeconds)
}

export function resetCamera(): void {
  $resetCameraSignal.set(Date.now())
}

export function setMorphWeight(key: string, value: number): void {
  $morphWeights.setKey(key, value)
}

export function resetAllMorphs(): void {
  const current = $morphWeights.get()
  const cleared: Record<string, number> = {}

  for (const k of Object.keys(current)) {
    cleared[k] = 0
  }

  $morphWeights.set(cleared)
  $lipSyncAmp.set(0)
}

export function setBackground(bg: ViewportBackground): void {
  $viewportOptions.setKey('background', bg)
}

export function toggleSkeleton(): void {
  $viewportOptions.setKey('showSkeleton', !$viewportOptions.get().showSkeleton)
}

/**
 * 骨骼/关节显示开关。
 * 打开任意一项时自动切换模型为半透明全息风格（否则骨骼被表皮完全遮挡），
 * 两项都关闭时自动还原原始材质。切换后用户仍可用 toggleHologram 单独覆盖。
 */
function syncHologramWithRig(): void {
  const opts = $viewportOptions.get()
  $viewportOptions.setKey('showHologram', opts.showBones || opts.showJoints)
}

export function toggleBones(): void {
  $viewportOptions.setKey('showBones', !$viewportOptions.get().showBones)
  syncHologramWithRig()
}

export function toggleJoints(): void {
  $viewportOptions.setKey('showJoints', !$viewportOptions.get().showJoints)
  syncHologramWithRig()
}

export function toggleHologram(): void {
  $viewportOptions.setKey('showHologram', !$viewportOptions.get().showHologram)
}

export function toggleGrid(): void {
  $viewportOptions.setKey('showGrid', !$viewportOptions.get().showGrid)
}

export function toggleAxes(): void {
  $viewportOptions.setKey('showAxes', !$viewportOptions.get().showAxes)
}

export function toggleWireframe(): void {
  $viewportOptions.setKey('showWireframe', !$viewportOptions.get().showWireframe)
}

export function setTransformMode(mode: TransformMode): void {
  $transformMode.set(mode)
}

export function setModelPosition(x: number, y: number, z: number): void {
  $modelTransform.setKey('position', { x, y, z })
}

export function setModelRotation(x: number, y: number, z: number): void {
  $modelTransform.setKey('rotation', { x, y, z })
}

export function setModelScale(scale: number): void {
  $modelTransform.setKey('scale', scale)
}

export function resetTransform(): void {
  $modelTransform.set({
    position: { x: 0, y: 0, z: 0 },
    rotation: { x: 0, y: 0, z: 0 },
    scale: 1.0
  })
}

export function triggerAutoGround(): void {
  $autoGroundSignal.set(Date.now())
}

export function triggerAutoCenter(): void {
  $autoCenterSignal.set(Date.now())
}

export function triggerNormalizeHeight(): void {
  $normalizeHeightSignal.set(Date.now())
}

export function rotatePreset(axis: 'x' | 'y' | 'z', deltaDeg: number): void {
  $rotatePresetSignal.set({ axis, deltaDeg })
}
