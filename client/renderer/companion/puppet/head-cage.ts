/** HeadCage — 脸面/头骨双表面三角控制笼（Phase 2）。
 *
 * 三个语义控制点（左颊 / 右颊 / 颅顶）+ 脸面深度 dF 与头骨深度 dS；
 * 每顶点绑定 = 笼三角形重心坐标 cb[3] + 脸面↔头骨混合 μ（dEff = μ·dF + (1-μ)·dS）。
 * deform 头部块改为「控制点位移 + 重心混合」：当前头转公式是仿射的，重心混合
 * 精确复现原输出（μ 取层离散深度时）；Phase 3 的深度曲线 / 远眼收窄 / 周边可见度
 * 只需扩展控制点位移项，逐顶点经 cb 自动平滑。
 */

import type { RigAnchors } from './puppet-types'

export interface HeadCage {
  px: [number, number, number]
  py: [number, number, number]
  dF: number
  dS: number
}

/** 从语义锚点推导三角控制笼：左右颊取眼线高度的两缘，颅顶取发际上方。 */
export function buildHeadCage(A: RigAnchors, dF: number, dS: number): HeadCage {
  const fw = A.face.x1 - A.face.x0
  const fh = A.face.y1 - A.face.y0
  const eyeY = A.eyeL ? (A.eyeL.y0 + A.eyeL.y1) / 2 : A.face.cy

  return {
    px: [A.face.x0 + fw * 0.06, A.face.x1 - fw * 0.06, A.face.cx],
    py: [eyeY, eyeY, A.face.y0 + fh * 0.08],
    dF,
    dS
  }
}

/** 顶点在笼三角形中的重心坐标（三角形外为外插；仿射位移场下外插仍精确）。 */
export function cageBary(cage: HeadCage, x: number, y: number, out: Float32Array, o: number): void {
  const [x0, x1, x2] = cage.px
  const [y0, y1, y2] = cage.py
  const det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
  const w1 = ((x - x0) * (y2 - y0) - (x2 - x0) * (y - y0)) / det
  const w2 = ((x2 - x0) * (y - y0) - (x - x0) * (y1 - y0)) / det
  out[o] = 1 - w1 - w2
  out[o + 1] = w1
  out[o + 2] = w2
}

/** 脸面↔头骨混合 μ：按层深度定位（1=纯脸面、0=纯头骨）。μ_base 使 dEff≈原层 depth，保证回归基线。 */
export function headBlendMu(dF: number, dS: number, dd: number): number {
  if (dS - dF < 1e-6) {
    return 1
  }

  return Math.min(1, Math.max(0, (dS - dd) / (dS - dF)))
}
