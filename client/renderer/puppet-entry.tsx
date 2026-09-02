/** Puppet 调试台入口 — 拖入/选择 PSD → 自动装配 → 待机动画 + 鼠标视线跟随。
 *
 * 开发页不进生产窗口（sprite.html），Phase 6 集成由 root.tsx 渲染分支承载。
 * `?autotest=1`：无头验证 — 装配 + 动画探针（眨眼/说话/呼吸/待机/视线跟随）结果写进 header。
 * `?stage=1`：无头验证 Phase 6 生产挂载 — 真实 PuppetStage + 驱动层（hitmap/情绪/
 * 显式视线），绕过后端直接注入内置 PSD 当签名 URL。
 */

import './styles.css'

import { StrictMode, useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { $spriteEmotion, setGazeTarget } from '@/companion/companion-store'
import { $mesh2dHitmap } from '@/companion/mesh2d/mesh2d-store'
import { $puppetInfo } from '@/companion/puppet/puppet-store'
import type { Rig } from '@/companion/puppet/puppet-types'
import { PuppetCanvas, type PuppetCanvasHandle } from '@/companion/puppet/PuppetCanvas'
import { PuppetStage } from '@/companion/puppet/PuppetStage'

/** ?stage=1 — 挂真实 PuppetStage 并注入内置 PSD；断言 hitmap 区域 + 情绪/视线驱动可视。 */
function PuppetStageApp() {
  const [status, setStatus] = useState('stage 模式：注入内置 PSD …')

  useEffect(() => {
    void (async () => {
      $puppetInfo.set({
        psdUrl: `${import.meta.env.BASE_URL}assets/seethrough_output.psd`,
        contentHash: 'stage-test',
        error: null
      })

      let hit: ((nx: number, ny: number) => { region: string } | null) | null = null

      for (let i = 0; i < 100; i++) {
        const cur = $mesh2dHitmap.get()

        if (cur) {
          hit = cur.hit

          break
        }

        await new Promise(resolve => setTimeout(resolve, 100))
      }

      if (!hit) {
        setStatus('STAGE_FAIL hitmap 未上线（装配失败？）')

        return
      }

      const regions = new Set<string>()

      for (let gy = 0.05; gy <= 0.95; gy += 0.04) {
        for (let gx = 0.05; gx <= 0.95; gx += 0.04) {
          const r = hit(gx, gy)

          if (r) {
            regions.add(r.region)
          }
        }
      }

      // 情绪 + 显式视线注入（$gazeTarget 走生产驱动路径）
      $spriteEmotion.set('happy')
      setGazeTarget({ nx: 0.9, ny: 0.1 })
      setStatus(`STAGE_OK regions=${[...regions].join('|')}`)
    })()
  }, [])

  return (
    <div className="relative h-screen w-screen bg-neutral-950">
      <PuppetStage />
      <span className="absolute top-2 left-4 z-10 text-xs text-body">{status}</span>
    </div>
  )
}

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
      <span className="w-24 shrink-0 text-muted">{label}</span>
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

const AUTOS = ['idle', 'rand', 'blink', 'talk', 'gaze'] as const
type AutoKey = (typeof AUTOS)[number]

const AUTO_LABEL: Record<AutoKey, string> = {
  idle: '待机摇摆',
  rand: '随机漫游',
  blink: '眨眼',
  talk: '自动说话',
  gaze: '视线跟随'
}

function PuppetDevApp(): React.JSX.Element {
  const handleRef = useRef<PuppetCanvasHandle>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState('拖入或选择 see-through 产出的 PSD')
  const [rig, setRig] = useState<Rig | null>(null)
  const [angleX, setAngleX] = useState(0)
  const [angleY, setAngleY] = useState(0)
  const [angleZ, setAngleZ] = useState(0)
  const [eyeOpen, setEyeOpen] = useState(1)
  const [mouthOpen, setMouthOpen] = useState(0)

  const [autos, setAutos] = useState<Record<AutoKey, boolean>>(
    () => Object.fromEntries(AUTOS.map(k => [k, true])) as Record<AutoKey, boolean>
  )

  const fileRef = useRef<HTMLInputElement>(null)

  const onStageMove = useCallback((ev: React.MouseEvent): void => {
    const rt = handleRef.current?.runtime
    const rect = stageRef.current?.getBoundingClientRect()

    if (!rt || !rect) {
      return
    }

    // 归一化到画布区 [-1,1]（y 向下）；纵向参考面部高度（上 35%）避免永远俯视
    const nx = Math.max(-1, Math.min(1, (ev.clientX - rect.left - rect.width / 2) / (rect.width / 2)))
    const ny = Math.max(-1, Math.min(1, (ev.clientY - rect.top - rect.height * 0.35) / (rect.height / 2)))
    rt.setGaze(nx, ny)
  }, [])

  const clearGaze = useCallback((): void => {
    handleRef.current?.runtime?.setGaze(null)
  }, [])

  const toggleAuto = useCallback(
    (key: AutoKey) =>
      (ev: React.ChangeEvent<HTMLInputElement>): void => {
        const v = ev.target.checked
        setAutos(a => ({ ...a, [key]: v }))
        const rt = handleRef.current?.runtime

        if (rt) {
          rt.auto[key] = v
        }
      },
    []
  )

  // ?autotest=1：无头验证用——自动载入 PSD 后跑动画探针，把装配+动画结果写进 header 供 --dump-dom 断言
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
        const rt = handleRef.current?.runtime

        if (!r || !rt) {
          throw new Error('runtime 未就绪')
        }

        let eyeMin = 1
        let blinkSeen = false
        let mouthMax = 0
        let breathMin = 1
        let breathMax = 0
        let angMin = 1
        let angMax = -1
        let skirtA = 0
        let skirtB = 0

        // advanceSim 确定性步进（固定 1/60），不依赖 rAF/虚拟时钟——无头下两者推进不同步
        for (let i = 0; i < 27; i++) {
          rt.advanceSim(0.3)

          if (i === 10) {
            rt.forceBlink()
          }

          // 裙双频：不同相位两次采样 bottomwear 位移应有变化
          if (i === 3) {
            skirtA = rt.layerShift('bottomwear')
          }

          if (i === 22) {
            skirtB = rt.layerShift('bottomwear')
          }

          const s = rt.snapshot()
          eyeMin = Math.min(eyeMin, s.eyeOpenL, s.eyeOpenR)
          blinkSeen = blinkSeen || s.blinkActive
          mouthMax = Math.max(mouthMax, s.mouthOpen)
          breathMin = Math.min(breathMin, s.breath)
          breathMax = Math.max(breathMax, s.breath)
          angMin = Math.min(angMin, s.angleX)
          angMax = Math.max(angMax, s.angleX)
        }

        // 头部运动中段采样：关风噪后量发束链梢-根偏差，直接断言次级弹簧链输出
        rt.auto.idle = false
        rt.auto.rand = false
        rt.setGaze(1, 0.35)
        rt.advanceSim(0.25)
        const chainS = rt.chainSwing('front hair')
        rt.auto.idle = true
        rt.auto.rand = true
        rt.advanceSim(0.95)
        const g = rt.snapshot()
        rt.setGaze(null)

        rt.target.mouthOpen = 0.9
        rt.advanceSim(0.4)
        const m = rt.snapshot()
        rt.target.mouthOpen = 0

        const ms = rt.meshStats()

        const flags = [
          `blink=${eyeMin < 0.4 || blinkSeen ? 1 : 0}`,
          `mouth=${mouthMax > 0.2 || m.mouthOpen > 0.7 ? 1 : 0}`,
          `breath=${breathMax - breathMin > 0.4 ? 1 : 0}`,
          `idle=${angMax - angMin > 0.08 ? 1 : 0}`,
          `gaze=${g.eyeX > 0.55 && g.angleX > 0.15 ? 1 : 0}`,
          `mesh=${ms.artmesh >= Math.max(1, ms.layers - 4) && ms.tris > 0 ? 1 : 0}`,
          `chain=${chainS > 0.12 ? 1 : 0}`,
          `skirt=${Math.abs(skirtB - skirtA) > 0.05 || (skirtA === 0 && skirtB === 0) ? 1 : 0}`
        ].join(' ')

        setStatus(
          `AUTOTEST_OK parts=${r.layers.length} warnings=${r.warnings.length} ${flags} meshstat=${ms.artmesh}/${ms.layers}am ${ms.verts}v${ms.tris}t talkmax=${mouthMax.toFixed(2)} bns=${[...new Set(r.layers.map(l => l.name))].join(',')}`
        )
      } catch (err) {
        setStatus(`AUTOTEST_FAIL ${err instanceof Error ? err.message : String(err)}`)
      }
    })()
  }, [])

  // ?pose=ax,ay,az：姿态定格（头转扫掠/Phase 5 姿态安全验证用）——关自动化、直写参数并推进到稳态
  useEffect(() => {
    const pose = new URLSearchParams(window.location.search).get('pose')

    if (!pose) {
      return
    }

    const [ax = 0, ay = 0, az = 0] = pose.split(',').map(Number)

    void (async () => {
      setStatus(`姿态定格 ax=${ax} ay=${ay} az=${az} …`)

      try {
        const res = await fetch(`${import.meta.env.BASE_URL}assets/seethrough_output.psd`)

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }

        const r = await handleRef.current?.loadPsd(await res.arrayBuffer())
        const rt = handleRef.current?.runtime

        if (!r || !rt) {
          throw new Error('runtime 未就绪')
        }

        for (const key of Object.keys(rt.auto) as (keyof typeof rt.auto)[]) {
          rt.auto[key] = false
        }

        rt.frozen = true
        rt.target.angleX = ax
        rt.target.angleY = ay
        rt.target.angleZ = az
        rt.advanceSim(1)
        const s = rt.snapshot()
        setStatus(`POSE ax=${ax} ay=${ay} az=${az} parts=${r.layers.length} body=${s.body.toFixed(2)}`)
      } catch (err) {
        setStatus(`POSE_FAIL ${err instanceof Error ? err.message : String(err)}`)
      }
    })()
  }, [])

  // ?poses=1：13 姿态安全验证（Phase 5）——冻结动画、关自动化，逐姿态定格并评估
  // 网格翻转/最大边拉伸，档位与最差值写进 header 供无头 --dump-dom 断言
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).get('poses')) {
      return
    }
    void (async () => {
      setStatus('13 姿态安全验证 …')

      try {
        const res = await fetch(`${import.meta.env.BASE_URL}assets/seethrough_output.psd`)

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }

        const r = await handleRef.current?.loadPsd(await res.arrayBuffer())
        const rt = handleRef.current?.runtime

        if (!r || !rt) {
          throw new Error('runtime 未就绪')
        }

        const POSES: [number, number, number][] = [
          [0, 0, 0],
          [1, 0, 0],
          [-1, 0, 0],
          [0, 1, 0],
          [0, -1, 0],
          [0, 0, 1],
          [0, 0, -1],
          [1, 1, 0],
          [-1, -1, 0],
          [1, -1, 0],
          [1, 1, 1],
          [-1, -1, -1],
          [0.55, 0, 0]
        ]

        for (const key of Object.keys(rt.auto) as (keyof typeof rt.auto)[]) {
          rt.auto[key] = false
        }

        rt.frozen = true
        // 动作缩放阶梯（PuppetLoom 规范）：1 → 0.85 → … → 0.25，取首个全姿态无翻转的档
        const LADDER = [1, 0.85, 0.7, 0.55, 0.4, 0.25]
        let scale = 0
        let flips = 0
        let stretch = 1
        let parts: string[] = []
        let worst = ''
        let minA = Infinity
        let first = ''

        for (const sc of LADDER) {
          flips = 0
          stretch = 1
          minA = Infinity
          parts = []
          const allBy: Record<string, number> = {}

          for (const [ax, ay, az] of POSES) {
            rt.target.angleX = ax * sc
            rt.target.angleY = ay * sc
            rt.target.angleZ = az * sc
            rt.advanceSim(1.5)
            const s = rt.poseSafety()
            flips = Math.max(flips, s.flips)
            stretch = Math.max(stretch, s.maxStretch)
            minA = Math.min(minA, s.flipMinA)
            parts.push(`${s.flips}f/${s.maxStretch.toFixed(2)}x`)

            for (const [k, v] of Object.entries(s.byLayer)) {
              allBy[k] = (allBy[k] ?? 0) + v
            }
          }

          worst = Object.entries(allBy)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 4)
            .map(([k, v]) => `${k}:${v}`)
            .join('|')
          scale = sc

          if (sc === LADDER[0]) {
            first = `first=[flips=${flips} minA=${minA === Infinity ? '-' : minA.toFixed(2)} ${worst}]`
          }

          if (flips === 0) {
            break
          }
        }

        setStatus(
          `POSES_OK n=13 tier=${rt.rigTier()} scale=${scale} flips=${flips} stretch=${stretch.toFixed(2)} ${first} ${parts.join(' ')}`
        )
      } catch (err) {
        setStatus(`POSES_FAIL ${err instanceof Error ? err.message : String(err)}`)
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
      <header className="flex items-baseline gap-3 border-b border-line-standard px-4 py-2">
        <h1 className="text-sm font-semibold text-white">Puppet 调试台</h1>
        <span className="text-xs text-faint">{status}</span>
        <span className="ml-auto text-[10px] text-faint">
          {rig ? `${rig.layers.length} parts / ${rig.warnings.length} warnings` : ''}
        </span>
      </header>
      <div className="flex min-h-0 flex-1">
        <div
          className="flex min-w-0 flex-1 items-center justify-center"
          onMouseLeave={clearGaze}
          onMouseMove={onStageMove}
          ref={stageRef}
        >
          <PuppetCanvas onRig={setRig} ref={handleRef} />
        </div>
        <aside className="flex w-64 shrink-0 flex-col gap-3 overflow-y-auto border-l border-line-standard p-4">
          <button
            className="rounded bg-rose-700 px-3 py-1.5 text-xs text-white hover:bg-rose-600"
            onClick={() => fileRef.current?.click()}
          >
            选择 PSD 文件
          </button>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
            {AUTOS.map(key => (
              <label className="flex items-center gap-1" key={key}>
                <input checked={autos[key]} onChange={toggleAuto(key)} type="checkbox" />
                {AUTO_LABEL[key]}
              </label>
            ))}
          </div>
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
          <p className="mt-auto text-[10px] leading-relaxed text-faint">
            在画布区移动鼠标：角色视线会跟随光标（眼先动、头跟随）。
          </p>
        </aside>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {new URLSearchParams(window.location.search).get('stage') ? <PuppetStageApp /> : <PuppetDevApp />}
  </StrictMode>
)
