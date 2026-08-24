/** Mesh2D 子区域命中检测。
 *
 * 设计要点（mesh2d README + PROTOCOL.md §1.4）：
 * - 内部只用归一化 [0, 1] 坐标；不存像素值。原因：sprite 窗口受 $spatialScale 缩放
 *   + 高 DPI 设备像素比变化，像素坐标会漂。
 * - 外部必须先归一化：nx = (e.clientX - rect.left) / rect.width,
 *   ny = (e.clientY - rect.top) / rect.height（SpriteStage 在 pointermove 已计算过）。
 * - Y 轴向下与 manifest pivot 一致，命中测试不翻转 Y。
 * - 实现：骨骼枢轴距离 + axis-aligned bbox 测试（CPU 轻量），不读 GPU buffer。
 * - 区域优先级：head > face > front_hair > back_hair > arm_L/R > body > skirt
 *   —— 头部区域小，命中头部时不希望被头发"吃掉"。
 */

import type { Manifest } from './mesh2d-runtime'

export type HitRegionName = 'head' | 'face' | 'arm_L' | 'arm_R' | 'body' | 'back_hair' | 'front_hair' | 'skirt'

export interface HitRegion {
  region: HitRegionName
  /** z 深度（同区域多点命中时取最近） */
  depth: number
}

interface NormalizedBBox {
  /** 区域名（hitmap 内部用） */
  name: HitRegionName
  /** 归一化 [0, 1] 左上角 (x, y) */
  minX: number
  minY: number
  /** 归一化 [0, 1] 右下角 (x, y) */
  maxX: number
  maxY: number
  /** 命中优先级（数值越大优先级越高，命中测试时按降序尝试） */
  priority: number
}

// 区域优先级常量（命中测试按降序）
const REGION_PRIORITY: Record<HitRegionName, number> = {
  head: 100,
  face: 95,
  front_hair: 80,
  back_hair: 75,
  arm_L: 60,
  arm_R: 60,
  body: 40,
  skirt: 30
}

interface MeshDef {
  name: string
  texture: string
  geometry_w: number
  geometry_h: number
  z_order: number
  origin?: [number, number]
  bones_influences: { bone: string; weight: number }[]
}

/** 把 manifest 的像素 pivot + mesh 尺寸归一化到 [0, 1] 坐标。 */
function buildNormalizedBoxes(manifest: Manifest): NormalizedBBox[] {
  const W = manifest.canvas.w
  const H = manifest.canvas.h
  const boxes: NormalizedBBox[] = []

  // bone → world pivot（按 parent 链累加）—— 在归一化坐标系下
  const boneWorldPivot = new Map<string, { x: number; y: number }>()
  const boneByName = new Map(manifest.skeleton.bones.map(b => [b.name, b]))

  // 第一遍：root → 递归
  for (const def of manifest.skeleton.bones) {
    let x = def.pivot[0]
    let y = def.pivot[1]
    let parent = def.parent

    while (parent && boneByName.has(parent)) {
      const pDef = boneByName.get(parent)!
      x += pDef.pivot[0]
      y += pDef.pivot[1]
      parent = pDef.parent
    }

    boneWorldPivot.set(def.name, { x, y })
  }

  // 第二遍：按 meshDef 生成 bbox（一个 mesh = 一个候选 hit region）
  // 这里按 mesh 的 name 映射到 region；约定见下：
  const meshToRegion: Record<string, HitRegionName> = {
    back_hair_mesh: 'back_hair',
    body_main_mesh: 'body',
    arm_L_mesh: 'arm_L',
    arm_R_mesh: 'arm_R',
    front_hair_mesh: 'front_hair',
    leg_L_mesh: 'body',
    leg_R_mesh: 'body'
    // 注：clothing / leg mesh 暂不映射到独立 region，归并到 body
  }

  for (const mesh of manifest.meshes as MeshDef[]) {
    const region = meshToRegion[mesh.name]

    if (!region) {
      continue
    }

    // origin 是 mesh 中心；geometry_w/h 是像素尺寸
    const cx = mesh.origin ? mesh.origin[0] : W / 2
    const cy = mesh.origin ? mesh.origin[1] : H / 2
    const halfW = mesh.geometry_w / 2
    const halfH = mesh.geometry_h / 2
    const minX = Math.max(0, (cx - halfW) / W)
    const maxX = Math.min(1, (cx + halfW) / W)
    const minY = Math.max(0, (cy - halfH) / H)
    const maxY = Math.min(1, (cy + halfH) / H)
    boxes.push({
      name: region,
      minX,
      minY,
      maxX,
      maxY,
      priority: REGION_PRIORITY[region]
    })
  }

  // 头部专用 bbox：基于 head bone 的 pivot，按身体比例推一个略小于 head 的矩形
  // —— 不读 GPU，避免 silhouette readback；效果足够区分"点头"和"点身体"。
  const headPivot = boneWorldPivot.get('head')

  if (headPivot) {
    // head bbox 高度约占画布的 18%，宽度约占 24%，Y 向下
    const headW = 0.24
    const headH = 0.18
    const cx = headPivot.x / W
    const cy = headPivot.y / H
    boxes.push({
      name: 'head',
      minX: Math.max(0, cx - headW / 2),
      minY: Math.max(0, cy - headH / 2),
      maxX: Math.min(1, cx + headW / 2),
      maxY: Math.min(1, cy + headH / 2),
      priority: REGION_PRIORITY.head
    })

    // face 子区域：head 中心略偏下（脸部在 head 下方）
    const faceW = 0.16
    const faceH = 0.1
    const faceCy = cy + 0.04
    boxes.push({
      name: 'face',
      minX: Math.max(0, cx - faceW / 2),
      minY: Math.max(0, faceCy - faceH / 2),
      maxX: Math.min(1, cx + faceW / 2),
      maxY: Math.min(1, faceCy + faceH / 2),
      priority: REGION_PRIORITY.face
    })
  }

  // skirt bbox：从 body_main 底部向下延伸到 ~85% 画布高度
  const bodyMain = boneWorldPivot.get('body_main')

  if (bodyMain) {
    const skirtTop = bodyMain.y / H + 0.3 // body_main pivot 之下 30% 高度起
    const skirtBottom = 0.85
    const skirtCx = bodyMain.x / W
    const skirtHW = 0.18
    boxes.push({
      name: 'skirt',
      minX: Math.max(0, skirtCx - skirtHW),
      minY: Math.min(1, skirtTop),
      maxX: Math.min(1, skirtCx + skirtHW),
      maxY: skirtBottom,
      priority: REGION_PRIORITY.skirt
    })
  }

  // 按优先级降序排序（命中测试时按此顺序）
  boxes.sort((a, b) => b.priority - a.priority)

  return boxes
}

export class Mesh2DHitmap {
  private boxes: NormalizedBBox[]

  constructor(manifest: Manifest) {
    this.boxes = buildNormalizedBoxes(manifest)
  }

  /** 测试归一化坐标 (nx, ny) 落在哪个区域。null = 落在 sprite 外或未匹配。 */
  hitRegion(nx: number, ny: number): HitRegion | null {
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1) {
      return null
    }

    for (const box of this.boxes) {
      if (nx >= box.minX && nx <= box.maxX && ny >= box.minY && ny <= box.maxY) {
        return { region: box.name, depth: box.priority }
      }
    }

    return null
  }

  /** 返回所有候选区域（用于调试 / 注册到 interactive-regions 总线）。 */
  regions(): NormalizedBBox[] {
    return this.boxes.slice()
  }

  /** 重新计算（manifest 变化时调用）。 */
  rebuild(manifest: Manifest): void {
    this.boxes = buildNormalizedBoxes(manifest)
  }
}
