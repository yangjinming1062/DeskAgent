// 启动进度规范化：跨主进程广播与渲染层水合共用——别名 `@boot-progress`。

import { clamp } from './runtime'

// 收口到 0–100 整数；非数与 NaN 一律视为 0，避免上游 NaN 透传把进度条钉死。
export function clampBootProgress(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }

  return clamp(Math.round(value), 0, 100)
}
