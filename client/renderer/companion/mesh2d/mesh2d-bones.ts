/** 弹簧+阻尼 jiggle 物理 — hair_back_root / skirt_root / bust 三个 bone 用。*/

export interface JiggleConfig {
  k: number
  c: number
  impulseDecay: number
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
  const displacement = state.offset - state.target
  const force = -cfg.k * displacement - cfg.c * state.velocity
  const velocity = state.velocity + force * dt
  const offset = state.offset + velocity * dt

  return { offset, velocity, target: state.target }
}

export function clampJiggleOffset(value: number, max: number = 5): number {
  return Math.max(-max, Math.min(max, value))
}
