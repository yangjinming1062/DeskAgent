/** Puppet 调试台入口 — Phase 0c 验证用：拖入/选择 PSD → 自动装配 → 待机动画。
 *
 * 开发页不进生产窗口（sprite.html），Phase 6 集成时由 root.tsx 渲染分支承载。
 */

import './styles.css'

import { StrictMode, useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import type { Rig } from '@/companion/puppet/puppet-types'
import { PuppetCanvas, type PuppetCanvasHandle } from '@/companion/puppet/PuppetCanvas'

function Slider({
  label,
  value,
  min,
  max,
  onChange
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-24 shrink-0 text-white/60">{label}</span>
      <input
        className="flex-1 accent-rose-500"
        max={max}
        min={min}
        onChange={e => onChange(parseFloat(e.target.value))}
        step={0.01}
        type="range"
        value={value}
      />
    </label>
  )
}

function PuppetDevApp() {
  const handleRef = useRef<PuppetCanvasHandle>(null)
  const [status, setStatus] = useState('拖入或选择 see-through 产出的 PSD')
  const [rig, setRig] = useState<Rig | null>(null)
  const [angleX, setAngleX] = useState(0)
  const [angleY, setAngleY] = useState(0)
  const [angleZ, setAngleZ] = useState(0)
  const [eyeOpen, setEyeOpen] = useState(1)
  const [mouthOpen, setMouthOpen] = useState(0)
  const fileRef = useRef<HTMLInputElement>(null)

  const onRig = useCallback((r: Rig) => {
    setRig(r)
  }, [])

  // ?autotest=1：无头验证用——挂载后自动载入内置 PSD，把装配结果写进 header 供 --dump-dom 断言
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).get('autotest')) {
      return
    }
    void (async () => {
      setStatus('载入内置测试 PSD …')

      try {
        const res = await fetch(`${import.meta.env.BASE_URL}assets/seethrough_output.psd`)

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }

        const r = await handleRef.current?.loadPsd(await res.arrayBuffer())
        setStatus(`AUTOTEST_OK parts=${r?.layers.length ?? 0} warnings=${r?.warnings.length ?? 0}`)
      } catch (err) {
        setStatus(`AUTOTEST_FAIL ${err instanceof Error ? err.message : String(err)}`)
      }
    })()
  }, [])

  const loadFile = useCallback(async (file: File) => {
    setStatus(`解析 ${file.name} …`)

    try {
      const r = await handleRef.current?.loadPsd(await file.arrayBuffer())

      if (!r) {
        throw new Error('runtime 未就绪')
      }

      setStatus(`${r.layers.length} 部件已装配${r.warnings.length ? `；${r.warnings.length} 条警告` : ''}`)
    } catch (err) {
      setStatus(`失败: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [])

  const setParam = useCallback(
    (key: 'angleX' | 'angleY' | 'angleZ' | 'eyeOpenL' | 'eyeOpenR' | 'mouthOpen', v: number) => {
      const rt = handleRef.current?.runtime

      if (!rt) {
        return
      }

      if (key === 'eyeOpenL' || key === 'eyeOpenR') {
        rt.target.eyeOpenL = v
        rt.target.eyeOpenR = v
      } else {
        rt.target[key] = v
      }
    },
    []
  )

  return (
    <div
      className="flex h-screen w-screen flex-col bg-neutral-950"
      onDragOver={e => e.preventDefault()}
      onDrop={e => {
        e.preventDefault()
        const f = e.dataTransfer.files[0]

        if (f && /\.psd$/i.test(f.name)) {
          void loadFile(f)
        }
      }}
    >
      <header className="flex items-baseline gap-3 border-b border-white/10 px-4 py-2">
        <h1 className="text-sm font-semibold text-white">Puppet 调试台</h1>
        <span className="text-xs text-white/40">{status}</span>
        <span className="ml-auto text-[10px] text-white/30">
          {rig ? `${rig.layers.length} parts / ${rig.warnings.length} warnings` : ''}
        </span>
      </header>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 items-center justify-center">
          <PuppetCanvas onRig={onRig} ref={handleRef} />
        </div>
        <aside className="flex w-64 shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 p-4">
          <button
            className="rounded bg-rose-700 px-3 py-1.5 text-xs text-white hover:bg-rose-600"
            onClick={() => fileRef.current?.click()}
          >
            选择 PSD 文件
          </button>
          <button
            className="rounded bg-neutral-700 px-3 py-1.5 text-xs text-white hover:bg-neutral-600"
            onClick={() => {
              void (async () => {
                setStatus('载入内置测试 PSD …')

                try {
                  const res = await fetch(`${import.meta.env.BASE_URL}assets/seethrough_output.psd`)

                  if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`)
                  }

                  await handleRef.current?.loadPsd(await res.arrayBuffer())
                  setStatus('内置 PSD 已装配')
                } catch (err) {
                  setStatus(`失败: ${err instanceof Error ? err.message : String(err)}`)
                }
              })()
            }}
          >
            载入内置测试 PSD
          </button>
          <input
            accept=".psd"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]

              if (f) {
                void loadFile(f)
              }
            }}
            ref={fileRef}
            type="file"
          />
          <Slider
            label="角度 X"
            max={1}
            min={-1}
            onChange={v => {
              setAngleX(v)
              setParam('angleX', v)
            }}
            value={angleX}
          />
          <Slider
            label="角度 Y"
            max={1}
            min={-1}
            onChange={v => {
              setAngleY(v)
              setParam('angleY', v)
            }}
            value={angleY}
          />
          <Slider
            label="角度 Z"
            max={1}
            min={-1}
            onChange={v => {
              setAngleZ(v)
              setParam('angleZ', v)
            }}
            value={angleZ}
          />
          <Slider
            label="眼睛开合"
            max={1}
            min={0}
            onChange={v => {
              setEyeOpen(v)
              setParam('eyeOpenL', v)
            }}
            value={eyeOpen}
          />
          <Slider
            label="嘴开合"
            max={1}
            min={0}
            onChange={v => {
              setMouthOpen(v)
              setParam('mouthOpen', v)
            }}
            value={mouthOpen}
          />
        </aside>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PuppetDevApp />
  </StrictMode>
)
