/** PuppetStage — puppet 的生产挂载与驱动层（Phase 6）。
 *
 * 数据源 $puppetInfo（kind=psd manifest 分流）。装配后五路驱动，全部走 PuppetRuntime
 * 的 target/auto 注入面（mesh2d 驱动层同构契约）：
 * - hitmap：当前帧部件网格精确命中 → $mesh2dHitmap —— SpriteStage 的 tap/hover/手势
 *   管线与 interaction.ts 区域语义原样复用（区域=最上层命中部件的映射）；
 * - 视线：窗口 pointermove → setGaze（$gazeTarget 显式目标优先，周期重注入续 TTL）；
 * - 说话：TTS 振幅接管 mouthOpen，静默后交还合成说话；
 * - 情绪：$spriteEmotion → 眉/嘴型/眼参数映射（mesh2d 无面部通道，puppet 独有）；
 * - 动作/交互：$spriteAction 通用语义子集 → 定时包络；hover 发区 → hairImpulse。
 * 装配失败调 setPuppetError 熄灭 $puppetReady，root 渲染级联落 mesh2d / 3D / 蛋。
 */

import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import { registerAmplitudeSink } from '@/companion/audio-track'
import { $gazeTarget, $spriteAction, $spriteActionQueue, $spriteEmotion } from '@/companion/companion-store'
import { probeInteractiveRegions } from '@/companion/interactive-regions'
import { setMesh2DHitmap } from '@/companion/mesh2d/mesh2d-store'
import { $spriteContentRect } from '@/companion/spatial'
import { $contextMenuOpen } from '@/companion/sprite/context-menu-store'
import { log } from '@/shared/lib/log'

import type { PuppetRuntime } from './puppet-runtime'
import { $puppetInfo, setPuppetError } from './puppet-store'
import type { Rig } from './puppet-types'
import { PuppetCanvas, type PuppetCanvasHandle } from './PuppetCanvas'

const REDUCED_MOTION_QUERY =
  typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null

// ---------------------------------------------------------------------------
// hitmap：当前帧部件网格精确命中 → mesh2d 交互区域
// ---------------------------------------------------------------------------

// 部件规范层名（bn，vendor SLOTS 全集）→ 交互区域（区域白名单见 PROTOCOL §1.4；
// gesture-tracker 与 interaction.ts 消费同名区域）。未列出的部件按 body。
const PART_REGION: Record<string, string> = {
  face: 'face',
  eyewhite: 'face',
  irides: 'face',
  eyelash: 'face',
  eye_close: 'face',
  eyebrow: 'face',
  nose: 'face',
  mouth_open: 'face',
  mouth_close: 'face',
  facedetail: 'face',
  ears: 'head',
  earwear: 'head',
  headwear: 'head',
  'front hair': 'front_hair',
  'back hair': 'back_hair',
  neck: 'body',
  body: 'body',
  skin: 'body',
  torso: 'body',
  arm: 'body',
  arms: 'body',
  leg: 'body',
  legs: 'body',
  topwear: 'body',
  handwear: 'body',
  footwear: 'body',
  bottomwear: 'skirt',
  legwear: 'skirt'
}

// ---------------------------------------------------------------------------
// 情绪 → 面部参数（只写 target 通道；眨眼自动化用 min 合成不会顶掉 squint）
// ---------------------------------------------------------------------------

type FaceParams = Partial<
  Pick<
    PuppetRuntime['target'],
    | 'mouthForm'
    | 'mouthOpen'
    | 'brow'
    | 'browAngSym'
    | 'eyeCY'
    | 'eyeScaleL'
    | 'eyeScaleR'
    | 'irisScale'
    | 'eyeOpenL'
    | 'eyeOpenR'
  >
>

const EMOTION_DEFAULTS: Required<FaceParams> = {
  mouthForm: 0,
  mouthOpen: 0,
  brow: 0,
  browAngSym: 0,
  eyeCY: 0,
  eyeScaleL: 1,
  eyeScaleR: 1,
  irisScale: 1,
  eyeOpenL: 1,
  eyeOpenR: 1
}

// 情绪词表与后端 BUILTIN_EMOTIONS（backend/services/chat/affect.py）对齐 + 少量别名
const EMOTION_PARAMS: Record<string, FaceParams> = {
  happy: { mouthForm: 0.8, brow: 0.25, eyeCY: 0.12 },
  joy: { mouthForm: 0.8, brow: 0.25, eyeCY: 0.12 },
  cheerful: { mouthForm: 0.7, brow: 0.2 },
  sad: { mouthForm: -0.6, brow: -0.35, browAngSym: 0.28 },
  surprised: { eyeScaleL: 1.12, eyeScaleR: 1.12, irisScale: 1.1, brow: 0.5, mouthOpen: 0.22 },
  excited: { mouthForm: 0.9, brow: 0.35, eyeCY: 0.15, eyeScaleL: 1.06, eyeScaleR: 1.06 },
  confused: { browAngSym: 0.35, eyeCY: 0.08, mouthForm: -0.2 },
  concerned: { brow: -0.25, browAngSym: 0.3, mouthForm: -0.35 },
  shy: { eyeOpenL: 0.72, eyeOpenR: 0.72, mouthForm: 0.35, eyeCY: 0.1 },
  proud: { browAngSym: -0.25, eyeCY: 0.06, mouthForm: 0.2 },
  grateful: { mouthForm: 0.6, brow: 0.2, eyeOpenL: 0.85, eyeOpenR: 0.85 },
  playful: { mouthForm: 0.7, browAngSym: -0.3, eyeCY: 0.1 },
  bored: { eyeOpenL: 0.55, eyeOpenR: 0.55, brow: -0.15, mouthForm: -0.15 },
  lonely: { mouthForm: -0.4, brow: -0.2, eyeCY: -0.08 },
  sleepy: { eyeOpenL: 0.45, eyeOpenR: 0.45, brow: -0.1, mouthOpen: 0.14 },
  curious: { brow: 0.3, irisScale: 1.05, browAngSym: 0.15 },
  embarrassed: { eyeOpenL: 0.7, eyeOpenR: 0.7, browAngSym: 0.4, mouthForm: 0.15 },
  apologetic: { brow: -0.3, browAngSym: 0.3, mouthForm: -0.3, eyeOpenL: 0.85, eyeOpenR: 0.85 },
  pout: { mouthForm: -0.5, browAngSym: 0.45, brow: -0.2, mouthOpen: 0.12 },
  angry: { brow: -0.6, browAngSym: 0.5, mouthForm: -0.3 },
  smug: { browAngSym: -0.35, eyeOpenL: 0.8, eyeOpenR: 0.8, mouthForm: 0.4 },
  scared: { eyeScaleL: 1.15, eyeScaleR: 1.15, irisScale: 1.08, brow: 0.4, mouthOpen: 0.18 },
  relieved: { eyeOpenL: 0.75, eyeOpenR: 0.75, brow: 0.15, mouthForm: 0.3 },
  neutral: {}
}

function applyEmotion(rt: PuppetRuntime, emotion: string | null): void {
  const params = emotion ? (EMOTION_PARAMS[emotion] ?? null) : EMOTION_PARAMS['neutral']!

  if (!params) {
    return // 未知情绪键不动面部（与 mesh2d 忽略未注册 action 同一策略）
  }

  for (const key of Object.keys(EMOTION_DEFAULTS) as (keyof typeof EMOTION_DEFAULTS)[]) {
    rt.target[key] = (params[key] ?? EMOTION_DEFAULTS[key]) as never
  }
}

// ---------------------------------------------------------------------------
// 动作 → 定时包络（通用语义子集；未注册键忽略）
// ---------------------------------------------------------------------------

interface ActionEnvelope {
  durMs: number
  /** 触发首帧一次性执行（冲量等） */
  onStart?: (rt: PuppetRuntime) => void
  /** k ∈ [0,1]；结束帧 k=1 后 touched 通道写回 ACTION_DEFAULTS */
  apply: (k: number, rt: PuppetRuntime) => void
}

const ACTION_DEFAULTS: Partial<Record<keyof PuppetRuntime['target'], number>> = {
  angleX: 0,
  angleY: 0,
  angleZ: 0,
  body: 0,
  eyeX: 0,
  eyeCY: 0,
  armY: 0,
  armPos: 0,
  eyeOpenL: 1,
  eyeOpenR: 1
}

// 键集与后端 manifest_exporter DEFAULT_ACTIONS 白名单对齐（PROTOCOL.md §3）；
// 未列出的白名单键按忽略处理（与 mesh2d 忽略未注册 action 同策略）。
const ACTIONS: Record<string, ActionEnvelope> = {
  wave_right: {
    durMs: 1300,
    apply: (k, rt) => {
      rt.target.armY = -Math.abs(Math.sin(k * Math.PI * 3)) * 0.85
      rt.target.angleZ = Math.sin(k * Math.PI) * 0.1
    }
  },
  wave_left: {
    durMs: 1300,
    apply: (k, rt) => {
      rt.target.armY = -Math.abs(Math.sin(k * Math.PI * 3)) * 0.85
      rt.target.angleZ = -Math.sin(k * Math.PI) * 0.1
    }
  },
  present_right: {
    durMs: 1600,
    apply: (k, rt) => {
      rt.target.armPos = Math.sin(Math.min(1, k * 1.4) * Math.PI) * 0.7
      rt.target.eyeX = 0.4 * Math.sin(k * Math.PI)
    }
  },
  present_left: {
    durMs: 1600,
    apply: (k, rt) => {
      rt.target.armPos = Math.sin(Math.min(1, k * 1.4) * Math.PI) * 0.7
      rt.target.eyeX = -0.4 * Math.sin(k * Math.PI)
    }
  },
  point_right: {
    durMs: 1400,
    apply: (k, rt) => {
      rt.target.armY = -Math.sin(Math.min(1, k * 1.5) * Math.PI) * 0.6
      rt.target.eyeX = 0.7 * Math.sin(k * Math.PI)
      rt.target.angleX = 0.3 * Math.sin(k * Math.PI)
    }
  },
  point_left: {
    durMs: 1400,
    apply: (k, rt) => {
      rt.target.armY = -Math.sin(Math.min(1, k * 1.5) * Math.PI) * 0.6
      rt.target.eyeX = -0.7 * Math.sin(k * Math.PI)
      rt.target.angleX = -0.3 * Math.sin(k * Math.PI)
    }
  },
  hands_on_hip: {
    durMs: 1800,
    apply: (k, rt) => {
      rt.target.armY = -0.5 * Math.sin(k * Math.PI)
      rt.target.body = 0.15 * Math.sin(k * Math.PI)
    }
  },
  hair_touch: {
    durMs: 1500,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      rt.target.armY = -0.6 * s
      rt.target.angleZ = 0.2 * s
      rt.target.eyeOpenL = 1 - 0.3 * s
      rt.target.eyeOpenR = 1 - 0.3 * s
    }
  },
  spread_arms: {
    durMs: 1500,
    apply: (k, rt) => {
      rt.target.armPos = -Math.sin(Math.min(1, k * 1.2) * Math.PI) * 0.8
    }
  },
  look_away_left: {
    durMs: 1600,
    apply: (k, rt) => {
      rt.target.eyeX = -0.85 * Math.sin(Math.min(1, k * 1.3) * Math.PI)
      rt.target.angleX = -0.35 * Math.sin(k * Math.PI)
    }
  },
  look_away_right: {
    durMs: 1600,
    apply: (k, rt) => {
      rt.target.eyeX = 0.85 * Math.sin(Math.min(1, k * 1.3) * Math.PI)
      rt.target.angleX = 0.35 * Math.sin(k * Math.PI)
    }
  },
  turn_body_left: {
    durMs: 1300,
    apply: (k, rt) => {
      rt.target.body = -Math.sin(k * Math.PI) * 0.5
      rt.target.angleX = -0.3 * Math.sin(k * Math.PI)
    }
  },
  turn_body_right: {
    durMs: 1300,
    apply: (k, rt) => {
      rt.target.body = Math.sin(k * Math.PI) * 0.5
      rt.target.angleX = 0.3 * Math.sin(k * Math.PI)
    }
  },
  lean_forward: {
    durMs: 1400,
    apply: (k, rt) => {
      rt.target.angleY = -Math.sin(k * Math.PI) * 0.4
      rt.target.body = Math.sin(k * Math.PI) * 0.2
    }
  },
  shy: {
    durMs: 2000,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      rt.target.eyeOpenL = 1 - 0.35 * s
      rt.target.eyeOpenR = 1 - 0.35 * s
      rt.target.angleZ = 0.25 * s
      rt.target.eyeCY = 0.1 * s
    }
  },
  idle_glance: {
    durMs: 1200,
    apply: (k, rt) => {
      rt.target.eyeX = Math.sin(k * Math.PI * 2) * 0.6
    }
  },
  petting: {
    durMs: 1600,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      rt.target.eyeOpenL = 1 - 0.55 * s
      rt.target.eyeOpenR = 1 - 0.55 * s
      rt.target.angleZ = 0.18 * s
    }
  },
  dizzy: {
    durMs: 2400,
    apply: (k, rt) => {
      rt.target.angleZ = Math.sin(k * Math.PI * 6) * 0.35 * (1 - k)
    }
  },
  peeking: {
    durMs: 1400,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      rt.target.eyeCY = 0.3 * s
      rt.target.eyeOpenL = 1 - 0.45 * s
      rt.target.eyeOpenR = 1 - 0.45 * s
      rt.target.angleY = 0.2 * s
    }
  },
  click: {
    durMs: 800,
    apply: (k, rt) => {
      rt.target.angleY = Math.sin(k * Math.PI * 2) * 0.3
    }
  },
  long_press: {
    durMs: 1200,
    apply: (k, rt) => {
      rt.target.eyeCY = Math.sin(k * Math.PI) * 0.25
    }
  },
  drag_end: {
    durMs: 900,
    apply: (k, rt) => {
      rt.target.angleZ = Math.sin(k * Math.PI * 3) * 0.25 * (1 - k)
    }
  }
}

// 可见内容包围盒上报：rig 层矩形并集（rig 坐标）→ 归一化舞台盒。canvas 经
// max-w/h-full 在舞台盒内 contain-fit 居中，rig 坐标按同一几何映射。
function publishPuppetContentRect(rig: Rig, container: HTMLElement | null): void {
  const boxW = container?.clientWidth || 0
  const boxH = container?.clientHeight || 0

  if (boxW <= 0 || boxH <= 0 || rig.layers.length === 0) {
    return
  }

  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity

  for (const L of rig.layers) {
    x0 = Math.min(x0, L.x)
    y0 = Math.min(y0, L.y)
    x1 = Math.max(x1, L.x + L.w)
    y1 = Math.max(y1, L.y + L.h)
  }

  const fit = Math.min(boxW / rig.canvas.w, boxH / rig.canvas.h)
  const offX = (boxW - rig.canvas.w * fit) / 2
  const offY = (boxH - rig.canvas.h * fit) / 2

  $spriteContentRect.set({
    left: (offX + x0 * fit) / boxW,
    top: (offY + y0 * fit) / boxH,
    right: (offX + x1 * fit) / boxW,
    bottom: (offY + y1 * fit) / boxH
  })
}

export function PuppetStage(): React.JSX.Element {
  const handleRef = useRef<PuppetCanvasHandle>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  // 命中换算需要画布的 contain-fit 矩形；挂载期一次性接线（稳定引用，避免触发运行时重建）
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const onCanvas = useCallback((canvas: HTMLCanvasElement): void => {
    canvasRef.current = canvas
  }, [])

  // 驱动层共享的运行态（rAF 循环每帧读取）
  const hitmapRef = useRef<((nx: number, ny: number) => { region: string } | null) | null>(null)
  const ampRef = useRef(0)
  const lastTalkAtRef = useRef(0)
  const wasTalkingRef = useRef(false)
  const gazeTargetRef = useRef<{ nx: number; ny: number } | null>(null)
  const lastGazeInjectRef = useRef(0)
  const lastHairImpulseAtRef = useRef(0)
  const actionRef = useRef<{ env: ActionEnvelope; t0: number } | null>(null)

  const puppet = useStore($puppetInfo)

  // PSD 装配 + hitmap 上线
  useEffect(() => {
    if (!puppet.psdUrl) {
      return
    }

    let cancelled = false

    void (async () => {
      try {
        // psdUrl 是相对后端的签名 URL——渲染进程 origin（dev 的 vite / 打包后的 file://）
        // 解析不了（vite 会回退 index.html，ag-psd 报 Invalid signature），须走主进程桥
        // 按 baseUrl 重写并带鉴权取字节；无桥环境（puppet 调试台 ?stage=1）注入的是
        // 同源资产 URL，直连 fetch 即可。
        let buffer: ArrayBuffer

        if (typeof window.spiritagent?.apiAssetBuffer === 'function') {
          buffer = (await window.spiritagent.apiAssetBuffer({ url: puppet.psdUrl! })).slice().buffer
        } else {
          // 无桥分支（puppet 调试台 ?stage=1 的纯浏览器上下文）：注入的是同源 vite 资产 URL。
          // eslint-disable-next-line no-restricted-syntax -- 非 Electron 调试上下文，URL 是同源资产而非后端相对路径
          const res = await fetch(puppet.psdUrl!)

          if (!res.ok) {
            throw new Error(`psd fetch failed: ${res.status}`)
          }

          buffer = await res.arrayBuffer()
        }

        const rig = await handleRef.current?.loadPsd(buffer)

        if (!rig || cancelled) {
          return
        }

        // 命中 = 当前帧可见像素：舞台归一化坐标先经 contain-fit 画布矩形换算成 rig
        // 画布像素，再由 runtime 自顶向下逐层网格点测（层矩形 bbox 会把部件四周的
        // 透明留白也算命中——透明窗口下即"看不见也能点"，见 companion README §7）。
        const hit = (nx: number, ny: number): { region: string } | null => {
          const rt = handleRef.current?.runtime
          const canvas = canvasRef.current
          const box = containerRef.current

          if (!rt || !canvas || !box) {
            return null
          }

          const br = box.getBoundingClientRect()
          const cr = canvas.getBoundingClientRect()

          if (cr.width <= 0 || cr.height <= 0) {
            return null
          }

          const cx = (nx * br.width + br.left - cr.left) / cr.width
          const cy = (ny * br.height + br.top - cr.top) / cr.height

          if (cx < 0 || cx > 1 || cy < 0 || cy > 1) {
            return null
          }

          const bn = rt.hitPart(cx * canvas.width, cy * canvas.height)

          return bn ? { region: PART_REGION[bn] ?? 'body' } : null
        }

        hitmapRef.current = hit
        setMesh2DHitmap({ hit })
        publishPuppetContentRect(rig, containerRef.current)
        probeInteractiveRegions()

        const rt = handleRef.current?.runtime

        if (rt) {
          if (REDUCED_MOTION_QUERY?.matches === true) {
            rt.auto.idle = false
            rt.auto.rand = false
          }

          log.info('puppet-stage', `psd rigged: ${rig.layers.length} parts, tier=${rt.rigTier()}`)
        }
      } catch (err) {
        log.warn('puppet-stage', 'psd load failed; cascade to mesh2d', err)
        setPuppetError(err instanceof Error ? err.message : String(err))
      }
    })()

    return () => {
      cancelled = true
      hitmapRef.current = null
      setMesh2DHitmap(null)
      $spriteContentRect.set(null)
      probeInteractiveRegions()
    }
  }, [puppet.psdUrl])

  // 驱动层：视线 / TTS / 情绪 / 动作 / hover 冲量 / 动作包络推进
  useEffect(() => {
    let raf = 0

    const rt = (): PuppetRuntime | null => handleRef.current?.runtime ?? null

    const onMove = (e: PointerEvent): void => {
      const r = rt()

      if (!r) {
        return
      }

      if ($contextMenuOpen.get()) {
        r.setGaze(null)

        return
      }

      const rect = containerRef.current?.getBoundingClientRect()

      if (!rect) {
        return
      }

      const lx = (e.clientX - rect.left) / rect.width
      const ly = (e.clientY - rect.top) / rect.height
      const nx = Math.max(-1, Math.min(1, lx * 2 - 1))
      // 纵向参考面部高度（画布上 35%），避免视线永远俯视
      const ny = Math.max(-1, Math.min(1, (ly - 0.35) * 2))
      r.setGaze(nx * 0.9, ny)

      // hover 发区 → 发束链冲量（200ms 节流）
      const hit = hitmapRef.current?.(lx, ly)
      const region = hit?.region

      if (
        (region === 'front_hair' || region === 'back_hair') &&
        performance.now() - lastHairImpulseAtRef.current > 200
      ) {
        lastHairImpulseAtRef.current = performance.now()
        r.hairImpulse((region === 'front_hair' ? 1.6 : 1.1) * (lx < 0.5 ? 1 : -1))
      }
    }

    const onLeave = (): void => {
      rt()?.setGaze(null)
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerleave', onLeave)

    const stopAmp = registerAmplitudeSink(amp => {
      ampRef.current = amp
    })

    const unsubEmotion = $spriteEmotion.listen(emotion => {
      const r = rt()

      if (r) {
        applyEmotion(r, emotion)
      }
    })

    const unsubAction = $spriteAction.listen(action => {
      const r = rt()

      if (!r) {
        return
      }

      const env = action ? ACTIONS[action] : undefined

      if (env) {
        env.onStart?.(r)
        actionRef.current = { env, t0: performance.now() }
      }
    })

    const unsubGazeTarget = $gazeTarget.listen(target => {
      gazeTargetRef.current = target

      if (target) {
        rt()?.setGaze(target.nx, target.ny)
      }
    })

    const tick = (now: number): void => {
      const r = rt()

      if (r) {
        // TTS 振幅接管嘴型；静默 600ms 后交还合成说话
        if (ampRef.current > 0.04) {
          lastTalkAtRef.current = now

          if (!wasTalkingRef.current) {
            wasTalkingRef.current = true
            r.auto.talk = false
          }

          r.target.mouthOpen = Math.min(1, 0.18 + ampRef.current * 0.9)
        } else if (wasTalkingRef.current && now - lastTalkAtRef.current > 600) {
          wasTalkingRef.current = false
          r.auto.talk = true
          r.target.mouthOpen = 0
        }

        // 显式视线目标周期重注入（setGaze 3s TTL；ritual walk 途中持续锁定）
        const gt = gazeTargetRef.current

        if (gt && now - lastGazeInjectRef.current > 800) {
          lastGazeInjectRef.current = now
          r.setGaze(gt.nx, gt.ny)
        }

        // 动作包络推进；结束帧把触及通道写回默认值并续播队列（mesh2d driver 同构）
        const a = actionRef.current

        if (a) {
          const k = (now - a.t0) / a.env.durMs

          if (k >= 1) {
            a.env.apply(1, r)

            for (const [key, v] of Object.entries(ACTION_DEFAULTS) as [keyof PuppetRuntime['target'], number][]) {
              r.target[key] = v as never
            }

            actionRef.current = null

            const queue = $spriteActionQueue.get()

            if (queue.length > 0) {
              const [next, ...rest] = queue
              $spriteActionQueue.set(rest)

              const env = next ? ACTIONS[next] : undefined

              if (env) {
                env.onStart?.(r)
                actionRef.current = { env, t0: now }
              }
            }
          } else {
            a.env.apply(k, r)
          }
        }
      }

      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerleave', onLeave)
      stopAmp()
      unsubEmotion()
      unsubAction()
      unsubGazeTarget()
    }
  }, [])

  return (
    <div
      className="flex h-full w-full items-center justify-center"
      ref={containerRef}
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
    >
      <PuppetCanvas onCanvas={onCanvas} ref={handleRef} />
    </div>
  )
}
