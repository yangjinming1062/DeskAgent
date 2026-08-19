import { useEffect, useRef } from 'react'

// `.current` 始终镜像最新 `value` 的稳定 ref——用于那些若直接捕获闭包
// 就会读到陈旧值的异步回调。
//
// 实现参考了 `react-use/useLatest` 与 `react-router` 内部的经典模式：
// 在每次渲染时赋值（适用于被事件处理器、计时器、Promise 引用
// 的 hooks 习惯写法），且不会触发额外渲染。
export function useLatestRef<T>(value: T): { readonly current: T } {
  const ref = useRef(value)

  useEffect(() => {
    ref.current = value
  }, [value])

  return ref
}
