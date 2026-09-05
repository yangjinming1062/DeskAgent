import type { ReadableAtom } from 'nanostores'
import { type DependencyList, useEffect } from 'react'

// 收拢"挂载时订阅 nanostores atom，卸载时取消"这一反复出现的样板。
// 取代 chat-panel / conversation-surface 中三处 `useEffect(() => $atom.listen(...))`。
// 签名取 `ReadableAtom<T>`——`.listen()` 是只读端点，写端点（`Atom`）也能向下兼容。
// 副作用初始化（如首次 scroll）若与订阅并存，留在调用方独立 effect 中——
// 本 hook 只吃订阅本身，不吸收额外副作用。
//
// Caller-managed deps：deps 由调用方按需提供，有意避开
// `react-hooks/exhaustive-deps` 与 React Compiler 的静态分析。
/* eslint-disable react-hooks/exhaustive-deps, react-compiler/react-compiler */
export function useAtomListen<T>($atom: ReadableAtom<T>, handler: (value: T) => void, deps: DependencyList): void {
  useEffect(() => {
    return $atom.listen(handler)
  }, deps)
}
/* eslint-enable react-hooks/exhaustive-deps, react-compiler/react-compiler */
