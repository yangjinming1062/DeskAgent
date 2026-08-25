/** PuppetCanvas — Puppet WebGL 运行时的 React 挂载壳。
 *
 * Phase 0c：PSD 驱动的静态/待机展示 + 开发参数面板（puppet.html 调试页使用）。
 * Phase 6 将接入 root.tsx 渲染分支与 drivers 情绪驱动。
 */

import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'

import { loadPsdIntoRuntime, PuppetRuntime } from './puppet-runtime'
import type { Rig } from './puppet-types'

export interface PuppetCanvasHandle {
  loadPsd(psdBuffer: ArrayBuffer): Promise<Rig>
  runtime: PuppetRuntime | null
}

interface Props {
  onRig?: (rig: Rig) => void
  onCanvas?: (canvas: HTMLCanvasElement) => void
}

export const PuppetCanvas = forwardRef<PuppetCanvasHandle, Props>(function PuppetCanvas({ onRig, onCanvas }, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<PuppetRuntime | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    onCanvas?.(canvas)
    let runtime: PuppetRuntime | null = null

    try {
      runtime = new PuppetRuntime(canvas)
      runtime.onRigApplied = onRig ?? null
      runtime.start()
      runtimeRef.current = runtime
    } catch (err) {
      console.error('puppet runtime init failed', err)
    }

    return () => {
      runtime?.dispose()
      runtimeRef.current = null
    }
    // onRig/onCanvas 由调用方保持稳定引用（挂载期一次性接线）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useImperativeHandle(
    ref,
    () => ({
      loadPsd: async (psdBuffer: ArrayBuffer) => {
        const runtime = runtimeRef.current

        if (!runtime) {
          throw new Error('puppet runtime not ready')
        }

        return await loadPsdIntoRuntime(runtime, psdBuffer)
      },
      get runtime() {
        return runtimeRef.current
      }
    }),
    []
  )

  return <canvas className="max-h-full max-w-full" ref={canvasRef} />
})
