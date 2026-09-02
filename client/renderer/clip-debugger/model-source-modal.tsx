import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import {
  decodeActivationCode,
  fetchBackendCompanion3DModelWithActivationCode,
  fetchGlbFromUrl,
  readGlbFile
} from './model-loader'
import { $customGlbBuffer, $modelStats } from './store'

interface ModelSourceModalProps {
  isOpen: boolean
  onClose: () => void
}

export function ModelSourceModal({ isOpen, onClose }: ModelSourceModalProps): React.JSX.Element | null {
  const [activeTab, setActiveTab] = useState<'backend' | 'url' | 'local'>('backend')

  const [activationCode, setActivationCode] = useState(
    () => localStorage.getItem('spiritagent_clip_activation_code') || ''
  )

  const [overrideHost, setOverrideHost] = useState(() => localStorage.getItem('spiritagent_clip_override_host') || '')

  const [directUrl, setDirectUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const customGlb = useStore($customGlbBuffer)
  const modelStats = useStore($modelStats)

  const runWithStatus = async <T,>(
    fn: () => Promise<T>,
    successMsg: string | ((result: T) => string),
    errorMsg: string,
    closeAfterMs?: number
  ): Promise<void> => {
    try {
      setLoading(true)
      setErrorMsg(null)
      setSuccessMsg(null)
      const result = await fn()
      setSuccessMsg(typeof successMsg === 'function' ? successMsg(result) : successMsg)

      if (closeAfterMs) {
        setTimeout(() => onClose(), closeAfterMs)
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : errorMsg)
    } finally {
      setLoading(false)
    }
  }

  if (!isOpen) {
    return null
  }

  // 尝试解析激活码中的后端地址用于展示提示
  let parsedHost = ''

  if (activationCode.trim()) {
    try {
      parsedHost = decodeActivationCode(activationCode.trim()).baseUrl
    } catch {
      // 忽略输入过程中的格式异常
    }
  }

  // 使用激活码一键拉取伴侣模型
  const handleFetchBackendModel = async () => {
    const trimmedCode = activationCode.trim()

    if (!trimmedCode) {
      setErrorMsg('请填入激活码')

      return
    }

    localStorage.setItem('spiritagent_clip_activation_code', trimmedCode)

    if (overrideHost) {
      localStorage.setItem('spiritagent_clip_override_host', overrideHost.trim())
    }

    await runWithStatus(
      async () => {
        const res = await fetchBackendCompanion3DModelWithActivationCode(trimmedCode, overrideHost.trim() || undefined)

        $customGlbBuffer.set({ buffer: res.buffer, name: res.name })

        return res
      },
      res => `成功拉取并载入伴侣模型: ${res.name}`,
      '获取后端模型失败',
      800
    )
  }

  const handleFetchDirectUrl = async () => {
    if (!directUrl.trim()) {
      return
    }

    await runWithStatus(
      async () => {
        const res = await fetchGlbFromUrl(directUrl.trim())
        $customGlbBuffer.set(res)

        return res
      },
      res => `成功载入模型: ${res.name}`,
      '下载并解析模型 URL 失败',
      800
    )
  }

  // 本地文件选择
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]

    if (file) {
      await runWithStatus(
        async () => {
          const res = await readGlbFile(file)
          $customGlbBuffer.set(res)

          return res
        },
        res => `成功载入本地模型: ${res.name}`,
        '解析本地模型失败',
        600
      )
    }
  }

  // 还原默认标准人偶
  const handleResetToMannequin = () => {
    $customGlbBuffer.set(null)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="flex w-full max-w-lg flex-col rounded-2xl border border-slate-700/80 bg-slate-900 shadow-2xl shadow-black/80">
        {/* 标题 */}
        <div className="flex items-center justify-between border-b border-slate-800 p-4">
          <div className="flex items-center gap-2">
            <span className="text-xl">🎯</span>
            <div>
              <h2 className="text-sm font-bold text-slate-100">选择 3D 模型来源 (Model Source)</h2>
              <p className="text-[11px] text-slate-400">支持拉取后端伴侣模型、远程 GLB 链接或本地文件</p>
            </div>
          </div>
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            onClick={onClose}
            type="button"
          >
            ✕
          </button>
        </div>

        {/* 标签栏 */}
        <div className="flex border-b border-slate-800 bg-slate-950/50 px-4 pt-2">
          <button
            className={`border-b-2 px-3 py-2 text-xs font-semibold transition-all ${
              activeTab === 'backend'
                ? 'border-sky-400 text-sky-300'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
            onClick={() => setActiveTab('backend')}
            type="button"
          >
            ☁️ 后端伴侣模型
          </button>

          <button
            className={`border-b-2 px-3 py-2 text-xs font-semibold transition-all ${
              activeTab === 'url'
                ? 'border-sky-400 text-sky-300'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
            onClick={() => setActiveTab('url')}
            type="button"
          >
            🔗 GLB 链接 / URL
          </button>

          <button
            className={`border-b-2 px-3 py-2 text-xs font-semibold transition-all ${
              activeTab === 'local'
                ? 'border-sky-400 text-sky-300'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
            onClick={() => setActiveTab('local')}
            type="button"
          >
            📁 本地模型文件
          </button>
        </div>

        {/* 内容区域 */}
        <div className="flex flex-col gap-4 p-5">
          {/* 当前加载状态提示 */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/60 px-3.5 py-2 text-xs">
            <span className="text-slate-400">当前模型:</span>
            <div className="flex items-center gap-2">
              <span className="font-mono font-medium text-sky-300">
                {customGlb ? customGlb.name : modelStats?.name || '标准骨骼人偶 (Mannequin)'}
              </span>
              {customGlb && (
                <button
                  className="text-xs text-amber-400 hover:underline"
                  onClick={handleResetToMannequin}
                  type="button"
                >
                  还原人偶
                </button>
              )}
            </div>
          </div>

          {activeTab === 'backend' && (
            <div className="flex flex-col gap-3">
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <label className="text-xs font-medium text-slate-300">
                    专属激活码 (Activation Code) <span className="text-sky-400">*</span>
                  </label>
                  {parsedHost && (
                    <span className="font-mono text-[10px] text-emerald-400">✓ 已识别后端: {parsedHost}</span>
                  )}
                </div>
                <textarea
                  className="h-20 w-full resize-none rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                  onChange={e => setActivationCode(e.target.value)}
                  placeholder="在此粘贴管理后台 (http://localhost:10620) 生成的激活码字符串..."
                  value={activationCode}
                />
                <span className="mt-1 block text-[10px] text-slate-500">
                  如客户端正常登录一样：系统会自动解密激活码并调用{' '}
                  <code className="text-sky-400">/api/user/activate</code> 兑换会话，随后拉取最新 3D 模型
                </span>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-400">自定义/覆盖后端地址 (可选)</label>
                <input
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600 focus:border-sky-500 focus:outline-none"
                  onChange={e => setOverrideHost(e.target.value)}
                  placeholder={parsedHost || '默认使用激活码内嵌地址'}
                  type="text"
                  value={overrideHost}
                />
              </div>

              <button
                className="mt-2 flex items-center justify-center gap-2 rounded-xl bg-sky-500 py-2.5 text-xs font-bold text-white shadow-lg shadow-sky-500/25 transition-all hover:bg-sky-400 disabled:opacity-50"
                disabled={loading || !activationCode.trim()}
                onClick={handleFetchBackendModel}
                type="button"
              >
                {loading ? '正在验证激活码并拉取模型中…' : '🚀 使用激活码一键拉取伴侣模型'}
              </button>
            </div>
          )}

          {activeTab === 'url' && (
            <div className="flex flex-col gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">GLB / GLTF 资源链接</label>
                <input
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                  onChange={e => setDirectUrl(e.target.value)}
                  placeholder="http://127.0.0.1:8000/api/companion/model/file/1/model.glb 或 https://..."
                  type="text"
                  value={directUrl}
                />
              </div>

              <button
                className="mt-2 flex items-center justify-center gap-2 rounded-xl bg-sky-500 py-2.5 text-xs font-bold text-white shadow-lg shadow-sky-500/25 transition-all hover:bg-sky-400 disabled:opacity-50"
                disabled={loading || !directUrl}
                onClick={handleFetchDirectUrl}
                type="button"
              >
                {loading ? '下载中…' : '📥 加载该 URL 模型'}
              </button>
            </div>
          )}

          {activeTab === 'local' && (
            <div className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-700 p-6 text-center">
              <span className="mb-2 text-3xl">📦</span>
              <p className="text-xs font-medium text-slate-200">选择本地 .glb / .gltf 模型文件</p>
              <p className="mt-1 text-[11px] text-slate-400">也支持在主视口中随时直接拖入文件</p>
              <label className="mt-3 cursor-pointer rounded-lg bg-slate-800 px-4 py-2 text-xs font-semibold text-sky-300 transition-colors hover:bg-slate-700">
                <span>浏览本地文件</span>
                <input accept=".glb,.gltf" className="hidden" onChange={handleFileSelect} type="file" />
              </label>
            </div>
          )}

          {/* 状态消息 */}
          {errorMsg && (
            <div className="rounded-lg border border-red-500/40 bg-red-950/60 p-2.5 text-xs text-red-300">
              ⚠️ {errorMsg}
            </div>
          )}

          {successMsg && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-950/60 p-2.5 text-xs text-emerald-300">
              ✨ {successMsg}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-slate-800 bg-slate-950/50 p-4">
          <button
            className="rounded-lg px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200"
            onClick={handleResetToMannequin}
            type="button"
          >
            使用内置标准骨骼人偶
          </button>

          <button
            className="rounded-lg bg-slate-800 px-4 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-700"
            onClick={onClose}
            type="button"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
