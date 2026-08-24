/** 弹簧+阻尼 jiggle 物理 — hair_back_root / skirt_root / bust 三个 bone 用。*/

export interface JiggleConfig {
  k: number
  c: number
  /** manifest 下发为 snake_case；旧 manifest 可能缺省，缺省时按中位默认值衰减。 */
  impulse_decay?: number
}

export interface JiggleState {
  offset: number
  velocity: number
  target: number
}

export function createJiggleState(): JiggleState {
  return { offset: 0, velocity: 0, target: 0 }
}

/** F = -k * (offset - target) - c * velocity；dt = 秒。返回新 offset。*/
export function stepJiggle(state: JiggleState, cfg: JiggleConfig, dt: number): JiggleState {
  // 冲量把 target 顶到 magnitude 后按 impulse_decay（按 60fps 归一）指数衰减回 0，
  // 弹簧随之回中——无衰减时 offset 会永久停在 target 上，头发/裙摆一次冲量后回不去。
  const target = state.target * Math.pow(cfg.impulse_decay ?? 0.93, dt * 60)
  const displacement = state.offset - target
  const force = -cfg.k * displacement - cfg.c * state.velocity
  const velocity = state.velocity + force * dt
  const offset = state.offset + velocity * dt

  return { offset, velocity, target }
}

export function clampJiggleOffset(value: number, max: number = 5): number {
  return Math.max(-max, Math.min(max, value))
}
