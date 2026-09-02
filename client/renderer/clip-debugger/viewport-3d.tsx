import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'

import { createProceduralMannequin, parseGlbBuffer, readGlbFile } from './model-loader'
import { createHologramMaterial, createSkeletonViz, type SkeletonViz } from './skeleton-viz'
import {
  $activeClip,
  $autoCenterSignal,
  $autoGroundSignal,
  $customGlbBuffer,
  $embeddedClips,
  $lipSyncAmp,
  $modelStats,
  $modelTransform,
  $morphWeights,
  $normalizeHeightSignal,
  $playbackState,
  $resetCameraSignal,
  $rotatePresetSignal,
  $scrubTime,
  $selectedRig,
  $stepFrameDelta,
  $transformMode,
  $viewportOptions,
  selectClip,
  setModelPosition,
  setModelRotation,
  setModelScale,
  triggerAutoGround
} from './store'
import { TransformToolbar } from './transform-toolbar'
import type { ClipItem } from './types'

export function Viewport3D(): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const viewportOpts = useStore($viewportOptions)
  const selectedRig = useStore($selectedRig)
  const customGlb = useStore($customGlbBuffer)
  const transformMode = useStore($transformMode)
  const modelTransform = useStore($modelTransform)

  // 场景与渲染核心引用
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const transformControlsRef = useRef<TransformControls | null>(null)
  const characterGroupRef = useRef<THREE.Group | null>(null)
  const skeletonHelperRef = useRef<THREE.SkeletonHelper | null>(null)
  const skeletonVizRef = useRef<SkeletonViz | null>(null)
  const hologramMatRef = useRef<THREE.MeshStandardMaterial | null>(null)
  const originalMaterialsRef = useRef<Map<THREE.Mesh, THREE.Material | THREE.Material[]>>(new Map())
  const gridHelperRef = useRef<THREE.GridHelper | null>(null)
  const axesHelperRef = useRef<THREE.AxesHelper | null>(null)

  const mixerRef = useRef<THREE.AnimationMixer | null>(null)
  const currentActionRef = useRef<THREE.AnimationAction | null>(null)
  const currentClipRef = useRef<THREE.AnimationClip | null>(null)
  const boneRestQuatsRef = useRef<Map<string, THREE.Quaternion>>(new Map())
  const loadedCharacterMeshesRef = useRef<THREE.Mesh[]>([])

  useEffect(() => {
    const container = containerRef.current

    if (!container) {
      return
    }

    const scene = new THREE.Scene()
    sceneRef.current = scene

    const width = container.clientWidth || 800
    const height = container.clientHeight || 600

    const camera = new THREE.PerspectiveCamera($viewportOptions.get().cameraFov, width / height, 0.1, 100)
    camera.position.set(0, 1.3, 3.5)
    cameraRef.current = camera

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.1
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    rendererRef.current = renderer

    container.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.set(0, 1.0, 0)
    controls.maxPolarAngle = Math.PI / 2 + 0.1
    controls.minDistance = 0.3
    controls.maxDistance = 25
    controlsRef.current = controls

    const transformControls = new TransformControls(camera, renderer.domElement)
    transformControls.size = 0.75

    transformControls.addEventListener('dragging-changed', event => {
      controls.enabled = !event.value
    })

    transformControls.addEventListener('objectChange', () => {
      if (characterGroupRef.current) {
        const obj = characterGroupRef.current
        $modelTransform.set({
          position: { x: obj.position.x, y: obj.position.y, z: obj.position.z },
          rotation: {
            x: THREE.MathUtils.radToDeg(obj.rotation.x),
            y: THREE.MathUtils.radToDeg(obj.rotation.y),
            z: THREE.MathUtils.radToDeg(obj.rotation.z)
          },
          scale: obj.scale.x
        })
      }
    })

    const helper =
      typeof transformControls.getHelper === 'function'
        ? transformControls.getHelper()
        : (transformControls as unknown as THREE.Object3D)

    scene.add(helper)
    transformControlsRef.current = transformControls

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6)
    scene.add(ambientLight)

    const mainLight = new THREE.DirectionalLight(0xffffff, 1.2)
    mainLight.position.set(3, 5, 4)
    mainLight.castShadow = true
    mainLight.shadow.mapSize.width = 2048
    mainLight.shadow.mapSize.height = 2048
    mainLight.shadow.bias = -0.0001
    scene.add(mainLight)

    const fillLight = new THREE.DirectionalLight(0x90caf9, 0.5)
    fillLight.position.set(-3, 3, -2)
    scene.add(fillLight)

    const rimLight = new THREE.DirectionalLight(0xffeedd, 0.7)
    rimLight.position.set(0, 4, -4)
    scene.add(rimLight)

    const grid = new THREE.GridHelper(10, 20, 0x64748b, 0x334155)
    grid.position.y = 0
    scene.add(grid)
    gridHelperRef.current = grid

    const axes = new THREE.AxesHelper(1.2)
    axes.position.set(0, 0.01, 0)
    scene.add(axes)
    axesHelperRef.current = axes

    let rafId: number
    const clock = new THREE.Clock()

    const animate = () => {
      rafId = requestAnimationFrame(animate)
      const delta = clock.getDelta()

      controls.update()

      const playback = $playbackState.get()

      if (mixerRef.current && playback.isPlaying) {
        mixerRef.current.update(delta * playback.speed)

        if (currentActionRef.current) {
          const action = currentActionRef.current
          const clipDuration = action.getClip().duration || 1
          const rawTime = action.time % clipDuration
          $playbackState.setKey('currentTime', Math.max(0, rawTime))
        }
      }

      // 骨骼可视化跟随当前姿态刷新（放在 mixer 之后，保证读到本帧最终骨骼矩阵）
      if (skeletonVizRef.current?.group.visible) {
        skeletonVizRef.current.update()
      }

      renderer.render(scene, camera)
    }

    animate()

    // 尺寸响应
    const handleResize = () => {
      if (!container || !renderer || !camera) {
        return
      }

      const w = container.clientWidth
      const h = container.clientHeight
      camera.aspect = w / (h || 1)
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }

    const ro = new ResizeObserver(handleResize)
    ro.observe(container)

    return () => {
      cancelAnimationFrame(rafId)
      ro.disconnect()
      controls.dispose()
      skeletonVizRef.current?.dispose()
      skeletonVizRef.current = null
      hologramMatRef.current?.dispose()
      hologramMatRef.current = null
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [])

  // 背景色与辅助线响应
  useEffect(() => {
    const scene = sceneRef.current
    const renderer = rendererRef.current
    const camera = cameraRef.current

    if (!scene || !renderer) {
      return
    }

    if (camera && camera.fov !== viewportOpts.cameraFov) {
      camera.fov = viewportOpts.cameraFov
      camera.updateProjectionMatrix()
    }

    if (gridHelperRef.current) {
      gridHelperRef.current.visible = viewportOpts.showGrid
    }

    if (axesHelperRef.current) {
      axesHelperRef.current.visible = viewportOpts.showAxes
    }

    if (skeletonHelperRef.current) {
      skeletonHelperRef.current.visible = viewportOpts.showSkeleton
    }

    if (skeletonVizRef.current) {
      skeletonVizRef.current.setVisible(viewportOpts.showBones, viewportOpts.showJoints)

      if (skeletonVizRef.current.group.visible) {
        skeletonVizRef.current.update()
      }
    }

    // 全息半透明风格：替换角色材质，关闭时还原原始材质
    if (viewportOpts.showHologram) {
      if (!hologramMatRef.current) {
        hologramMatRef.current = createHologramMaterial()
      }

      for (const m of loadedCharacterMeshesRef.current) {
        if (!originalMaterialsRef.current.has(m)) {
          originalMaterialsRef.current.set(m, m.material)
        }

        m.material = hologramMatRef.current
        m.castShadow = false
      }
    } else if (originalMaterialsRef.current.size > 0) {
      for (const [mesh, mat] of originalMaterialsRef.current) {
        mesh.material = mat
        mesh.castShadow = true
      }

      originalMaterialsRef.current.clear()
    }

    // 线框模式切换（全息模式下作用于全息材质本身）
    for (const m of loadedCharacterMeshesRef.current) {
      const mats = Array.isArray(m.material) ? m.material : [m.material]

      for (const mat of mats) {
        if ('wireframe' in mat) {
          ;(mat as THREE.MeshStandardMaterial).wireframe = viewportOpts.showWireframe
        }
      }
    }

    // 背景样式
    switch (viewportOpts.background) {
      case 'studio':
        scene.background = new THREE.Color(0x0f172a)

        break

      case 'slate':
        scene.background = new THREE.Color(0x1e293b)

        break

      case 'midnight':
        scene.background = new THREE.Color(0x090d16)

        break

      case 'light':
        scene.background = new THREE.Color(0xf1f5f9)

        break

      case 'transparent':
        scene.background = null

        break
    }
  }, [viewportOpts])

  // 相机重置信号
  const resetCameraSig = useStore($resetCameraSignal)
  useEffect(() => {
    if (!resetCameraSig || !cameraRef.current || !controlsRef.current) {
      return
    }

    const char = characterGroupRef.current
    let targetY = 1.0
    let camDistance = 3.5

    if (char) {
      const box = new THREE.Box3().setFromObject(char)

      if (!box.isEmpty()) {
        const height = Math.max(0.2, box.max.y - box.min.y)
        targetY = (box.min.y + box.max.y) * 0.55
        camDistance = Math.max(1.8, height * 2.1)
      }
    }

    controlsRef.current.target.set(0, targetY, 0)
    cameraRef.current.position.set(0, targetY + 0.15, camDistance)
    controlsRef.current.update()
  }, [resetCameraSig])

  useEffect(() => {
    const tc = transformControlsRef.current
    const char = characterGroupRef.current

    if (!tc) {
      return
    }

    if (transformMode === 'view' || !char) {
      tc.detach()
    } else {
      tc.attach(char)
      tc.setMode(transformMode)
    }
  }, [transformMode])

  // 模型位移/旋转/缩放响应
  useEffect(() => {
    const char = characterGroupRef.current

    if (!char) {
      return
    }

    char.position.set(modelTransform.position.x, modelTransform.position.y, modelTransform.position.z)
    char.rotation.set(
      THREE.MathUtils.degToRad(modelTransform.rotation.x),
      THREE.MathUtils.degToRad(modelTransform.rotation.y),
      THREE.MathUtils.degToRad(modelTransform.rotation.z)
    )
    char.scale.setScalar(modelTransform.scale)
  }, [modelTransform])

  // 快捷接地信号
  const autoGroundSig = useStore($autoGroundSignal)
  useEffect(() => {
    if (!autoGroundSig || !characterGroupRef.current) {
      return
    }

    const char = characterGroupRef.current
    const box = new THREE.Box3().setFromObject(char)

    if (!box.isEmpty()) {
      const deltaY = -box.min.y
      setModelPosition(char.position.x, char.position.y + deltaY, char.position.z)
    }
  }, [autoGroundSig])

  // 快捷居中信号
  const autoCenterSig = useStore($autoCenterSignal)
  useEffect(() => {
    if (!autoCenterSig || !characterGroupRef.current) {
      return
    }

    const char = characterGroupRef.current
    const box = new THREE.Box3().setFromObject(char)

    if (!box.isEmpty()) {
      const centerX = (box.min.x + box.max.x) / 2
      const centerZ = (box.min.z + box.max.z) / 2
      setModelPosition(char.position.x - centerX, char.position.y, char.position.z - centerZ)
    }
  }, [autoCenterSig])

  // 高度归一化信号 (自适应缩放到 1.7m)
  const normalizeHeightSig = useStore($normalizeHeightSignal)
  useEffect(() => {
    if (!normalizeHeightSig || !characterGroupRef.current) {
      return
    }

    const char = characterGroupRef.current
    const box = new THREE.Box3().setFromObject(char)

    if (!box.isEmpty()) {
      const currentH = box.max.y - box.min.y

      if (currentH > 0.05) {
        const factor = 1.7 / currentH
        setModelScale(char.scale.x * factor)
        setTimeout(() => triggerAutoGround(), 50)
      }
    }
  }, [normalizeHeightSig])

  // 快捷预设旋转信号 (立起 / 转身)
  const rotatePresetSig = useStore($rotatePresetSignal)
  useEffect(() => {
    if (!rotatePresetSig || !characterGroupRef.current) {
      return
    }

    const cur = $modelTransform.get().rotation
    const nextRot = { ...cur }
    nextRot[rotatePresetSig.axis] = (nextRot[rotatePresetSig.axis] + rotatePresetSig.deltaDeg) % 360
    setModelRotation(nextRot.x, nextRot.y, nextRot.z)
    setTimeout(() => triggerAutoGround(), 50)
  }, [rotatePresetSig])

  useEffect(() => {
    const scene = sceneRef.current
    const controls = controlsRef.current
    const camera = cameraRef.current

    if (!scene || !controls || !camera) {
      return
    }

    let isStale = false

    const loadCharacter = async () => {
      try {
        setLoadError(null)

        // 卸载旧角色
        if (characterGroupRef.current) {
          if (transformControlsRef.current) {
            transformControlsRef.current.detach()
          }

          scene.remove(characterGroupRef.current)
          characterGroupRef.current = null
        }

        if (skeletonHelperRef.current) {
          scene.remove(skeletonHelperRef.current)
          skeletonHelperRef.current = null
        }

        if (skeletonVizRef.current) {
          skeletonVizRef.current.dispose()
          skeletonVizRef.current = null
        }

        originalMaterialsRef.current.clear()

        if (mixerRef.current) {
          mixerRef.current.stopAllAction()
          mixerRef.current = null
        }

        currentActionRef.current = null
        currentClipRef.current = null

        let parsed

        if (customGlb) {
          parsed = await parseGlbBuffer(customGlb.buffer, customGlb.name)
        } else {
          parsed = createProceduralMannequin(selectedRig)
        }

        if (isStale) {
          return
        }

        scene.add(parsed.root)
        characterGroupRef.current = parsed.root
        boneRestQuatsRef.current = parsed.boneRestQuats

        // 收集 meshes
        const meshes: THREE.Mesh[] = []
        parsed.root.traverse(child => {
          if (child instanceof THREE.Mesh) {
            meshes.push(child)
          }
        })
        loadedCharacterMeshesRef.current = meshes

        // 智能包围盒分析：自动立起平躺模型、自动贴地、自动居中、自适应相机焦点
        const box = new THREE.Box3().setFromObject(parsed.root)
        let initRotX = 0

        if (!box.isEmpty()) {
          const size = new THREE.Vector3()
          box.getSize(size)

          // 诊断是否平躺 (Z 轴跨度显著大于 Y 轴高度且 Y 高度很扁)
          if (size.z > size.y * 1.3 && size.y < 0.7) {
            initRotX = 90
            parsed.root.rotation.x = THREE.MathUtils.degToRad(initRotX)
            box.setFromObject(parsed.root)
            box.getSize(size)
          }

          const height = Math.max(0.1, size.y)
          const initialPosY = -box.min.y // 双脚贴地对齐 Y=0
          const initialPosX = -(box.min.x + box.max.x) / 2 // 水平居中
          const initialPosZ = -(box.min.z + box.max.z) / 2

          parsed.root.position.set(initialPosX, initialPosY, initialPosZ)

          $modelTransform.set({
            position: { x: initialPosX, y: initialPosY, z: initialPosZ },
            rotation: { x: initRotX, y: 0, z: 0 },
            scale: 1.0
          })

          // 自动平视居中相机
          const targetY = height * 0.55
          controls.target.set(0, targetY, 0)
          camera.position.set(0, targetY + 0.15, Math.max(1.8, height * 2.1))
          controls.update()
        }

        // 骨骼辅助线
        const skeletonHelper = new THREE.SkeletonHelper(parsed.root)
        skeletonHelper.visible = $viewportOptions.get().showSkeleton
        scene.add(skeletonHelper)
        skeletonHelperRef.current = skeletonHelper

        // 关节球 / 实体骨段可视化（世界空间叠加层）
        const viz = createSkeletonViz(parsed.root)

        if (viz) {
          const opts = $viewportOptions.get()
          viz.setVisible(opts.showBones, opts.showJoints)
          scene.add(viz.group)
          skeletonVizRef.current = viz
        }

        // 若当前处于全息模式，新载入的模型同样应用全息材质
        if ($viewportOptions.get().showHologram) {
          if (!hologramMatRef.current) {
            hologramMatRef.current = createHologramMaterial()
          }

          for (const m of meshes) {
            originalMaterialsRef.current.set(m, m.material)
            m.material = hologramMatRef.current
            m.castShadow = false
          }
        }

        // 动画 Mixer
        const mixer = new THREE.AnimationMixer(parsed.root)
        mixerRef.current = mixer

        // 发布模型信息与内置动画
        $modelStats.set(parsed.stats)
        $embeddedClips.set(parsed.embeddedClips)

        const currentActive = $activeClip.get()

        if (currentActive) {
          playTargetClip(currentActive, mixer, 0)
        } else if (parsed.embeddedClips.length > 0) {
          selectClip(parsed.embeddedClips[0])
        }
      } catch (err) {
        if (!isStale) {
          setLoadError(err instanceof Error ? err.message : '加载模型失败')
        }
      }
    }

    void loadCharacter()

    return () => {
      isStale = true
    }
  }, [selectedRig, customGlb])

  const playTargetClip = (item: ClipItem, mixer: THREE.AnimationMixer, fadeDuration = 0.25) => {
    const animClip = item.animationClip

    if (!animClip) {
      return
    }

    const nextAction = mixer.clipAction(animClip)
    const playback = $playbackState.get()

    // 循环模式
    let shouldLoop = item.loop

    if (playback.loopMode === 'force-loop') {
      shouldLoop = true
    } else if (playback.loopMode === 'force-once') {
      shouldLoop = false
    }

    nextAction.setLoop(shouldLoop ? THREE.LoopRepeat : THREE.LoopOnce, shouldLoop ? Infinity : 1)
    nextAction.clampWhenFinished = true
    nextAction.setEffectiveTimeScale(playback.speed)
    nextAction.setEffectiveWeight(1)

    if (currentActionRef.current && currentActionRef.current !== nextAction && fadeDuration > 0) {
      currentActionRef.current.crossFadeTo(nextAction, fadeDuration, false)
    } else {
      nextAction.reset()
    }

    nextAction.play()
    currentActionRef.current = nextAction
    currentClipRef.current = animClip

    $playbackState.setKey('duration', animClip.duration)
  }

  const activeClip = useStore($activeClip)
  useEffect(() => {
    if (!activeClip || !mixerRef.current) {
      return
    }

    playTargetClip(activeClip, mixerRef.current, $playbackState.get().crossFadeDuration)
  }, [activeClip])

  const scrubTime = useStore($scrubTime)
  useEffect(() => {
    if (scrubTime === null || !currentActionRef.current || !mixerRef.current) {
      return
    }

    const action = currentActionRef.current
    action.time = Math.max(0, Math.min(scrubTime, action.getClip().duration))
    mixerRef.current.update(0)
  }, [scrubTime])

  const stepDelta = useStore($stepFrameDelta)
  useEffect(() => {
    if (stepDelta === null || !currentActionRef.current || !mixerRef.current) {
      return
    }

    const action = currentActionRef.current
    const dur = action.getClip().duration
    const nextT = Math.max(0, Math.min((action.time + stepDelta + dur) % dur, dur))
    action.time = nextT
    mixerRef.current.update(0)
    $playbackState.setKey('currentTime', nextT)
  }, [stepDelta])

  const morphWeights = useStore($morphWeights)
  const lipSyncAmp = useStore($lipSyncAmp)
  useEffect(() => {
    for (const mesh of loadedCharacterMeshesRef.current) {
      if (mesh.morphTargetDictionary && mesh.morphTargetInfluences) {
        for (const [name, weight] of Object.entries(morphWeights)) {
          const idx = mesh.morphTargetDictionary[name]

          if (idx !== undefined) {
            mesh.morphTargetInfluences[idx] = weight
          }
        }

        // Lip sync 嘴型振幅
        const mouthIdx =
          mesh.morphTargetDictionary['mouth_open'] ??
          mesh.morphTargetDictionary['jawOpen'] ??
          mesh.morphTargetDictionary['viseme_aa']

        if (mouthIdx !== undefined && lipSyncAmp > 0) {
          mesh.morphTargetInfluences[mouthIdx] = Math.max(mesh.morphTargetInfluences[mouthIdx] || 0, lipSyncAmp)
        }
      }
    }
  }, [morphWeights, lipSyncAmp])

  // 拖拽外部 GLB 文件处理
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)

    const files = Array.from(e.dataTransfer.files)
    const glbFile = files.find(f => f.name.endsWith('.glb') || f.name.endsWith('.gltf'))

    if (glbFile) {
      try {
        const loaded = await readGlbFile(glbFile)
        $customGlbBuffer.set(loaded)
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : '解析拖拽模型文件失败')
      }
    }
  }

  return (
    <div
      className="relative flex h-full w-full flex-col overflow-hidden bg-slate-950 select-none"
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* 3D Canvas Container */}
      <div className="h-full w-full" ref={containerRef} />

      {/* 视角与模型坐标轴变换悬浮工具栏 */}
      <TransformToolbar />

      {/* 拖拽提示层 */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex flex-col items-center justify-center bg-sky-950/80 backdrop-blur-md transition-all">
          <div className="flex flex-col items-center rounded-2xl border-2 border-dashed border-sky-400 p-8 shadow-2xl">
            <span className="mb-3 text-5xl">📦</span>
            <span className="text-lg font-bold text-sky-100">释放鼠标载入 3D 模型 (.glb / .gltf)</span>
            <span className="mt-1 text-xs text-sky-300">将自动提取骨骼、动作片段与表情 Blendshapes</span>
          </div>
        </div>
      )}

      {/* 错误提示 */}
      {loadError && (
        <div className="absolute top-16 left-4 z-40 flex items-center gap-2 rounded-lg border border-red-500/50 bg-red-950/80 px-4 py-2 text-xs text-red-200 backdrop-blur-md">
          <span>⚠️ {loadError}</span>
          <button className="ml-2 underline hover:text-white" onClick={() => $customGlbBuffer.set(null)} type="button">
            还原默认模型
          </button>
        </div>
      )}
    </div>
  )
}
