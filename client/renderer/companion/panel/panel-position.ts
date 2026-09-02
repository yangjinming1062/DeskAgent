// 面板几何常量：居中安全区（距视口边缘的最小内边距与轴向预留）。
const SAFE_MARGIN_PX = 16
const SAFE_INSET_PX = SAFE_MARGIN_PX * 2

export interface PanelPosition {
  left: number
  top: number
}

// 视口内居中：面板尺寸超过视口减去安全内边距时按可容纳尺寸收敛，圆整后下钳到 SAFE_MARGIN_PX。
// 共享给 floating-panel 与 chat-dock 的初始居中坐标计算。
export function centeredPanelPosition(
  viewport: { width: number; height: number },
  size: { width: number; height: number }
): PanelPosition {
  return {
    left: Math.max(
      SAFE_MARGIN_PX,
      Math.round((viewport.width - Math.min(viewport.width - SAFE_INSET_PX, size.width)) / 2)
    ),
    top: Math.max(
      SAFE_MARGIN_PX,
      Math.round((viewport.height - Math.min(viewport.height - SAFE_INSET_PX, size.height)) / 2)
    )
  }
}
