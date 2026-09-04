// 面板居中安全边距与轴向预留（floating-panel / chat-dock 共用居中公式）。
const SAFE_MARGIN_PX = 16
const SAFE_INSET_PX = SAFE_MARGIN_PX * 2

export interface PanelPosition {
  left: number
  top: number
}

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
