import type React from 'react'
import { useState } from 'react'
import { useStore } from '@nanostores/react'
import { Button } from '../components/button'
import { Egg, type EggPhase } from '../components/egg'
import { Halo } from '../components/halo'
import { HatchOverlay } from '../components/hatch-overlay'
import { StageList } from '../components/stage-list'
import {
  cancelInstall,
  $progress,
  type BootstrapStateModel
} from '../store'
import { FileText, ChevronRight, Loader2 } from 'lucide-react'
import clsx from 'clsx'

interface ProgressProps {
  bootstrap: BootstrapStateModel
}

export default function ProgressScreen({ bootstrap }: ProgressProps): React.JSX.Element {
  const progress = useStore($progress)
  const [showDetails, setShowDetails] = useState(false)

  const stageOrder = bootstrap.stageOrder
  const currentStageName = bootstrap.currentStage
  const currentStageIdx = currentStageName ? stageOrder.indexOf(currentStageName) : -1

  const currentStageObj = currentStageName ? bootstrap.stages[currentStageName] : null

  // Determine egg phase
  let phase: EggPhase = 'idle'
  if (bootstrap.status === 'completed') {
    phase = 'hatching'
  } else if (bootstrap.status === 'failed') {
    phase = 'failed'
  } else if (progress.done > 0) {
    phase = 'cracking'
  }

  const failedIdx = stageOrder.findIndex((name) => bootstrap.stages[name]?.state === 'failed')
  const failedAt = failedIdx >= 0 ? failedIdx : null

  const captionText =
    bootstrap.status === 'completed'
      ? '破壳而生，准备就绪！'
      : bootstrap.status === 'failed'
        ? '安装中断，遇到了麻烦'
        : currentStageObj
          ? currentStageObj.info.title
          : '准备中…'

  return (
    <div className="spiritagent-fade-in relative isolate flex h-full flex-col overflow-hidden bg-background">
      {/* Background ambient glow */}
      <span aria-hidden="true" className="spiritagent-glow" />

      {/* Region A: Top Step Header */}
      <div className="flex shrink-0 items-center justify-between px-8 pt-6 pb-2">
        <div className="flex items-center gap-2">
          <span className="font-['Collapse'] text-lg font-bold tracking-[0.08em] text-primary">
            SPIRITAGENT
          </span>
          <span className="text-xs text-muted-foreground/60">|</span>
          <span className="text-xs font-medium text-foreground/80">
            {bootstrap.status === 'completed'
              ? '完成'
              : bootstrap.status === 'failed'
                ? '失败'
                : '安装中'}
          </span>
        </div>
        <div className="text-xs font-medium text-muted-foreground">
          第 {progress.done} 步 · 共 {progress.total} 步
        </div>
      </div>

      {/* Region B: Hero Egg + Halo (flex-1) */}
      <div className="relative flex flex-1 flex-col items-center justify-center min-h-0 px-6 py-2">
        <div className="relative flex items-center justify-center">
          {/* Outer Segmented Halo */}
          <Halo
            total={progress.total || 6}
            done={progress.done}
            runningIdx={bootstrap.status === 'running' && currentStageIdx >= 0 ? currentStageIdx : null}
            failedAt={failedAt}
            size={300}
            className="absolute"
          />

          {/* Center Egg */}
          <Egg
            cracks={progress.done}
            phase={phase}
            size={240}
          />

          {/* Hatch overlay centered on Hero */}
          <HatchOverlay active={bootstrap.status === 'completed'} />
        </div>

        {/* Dynamic Caption below Hero */}
        <div className="mt-4 flex items-center gap-2 text-center text-sm font-medium text-foreground/90">
          {bootstrap.status === 'running' && (
            <Loader2 size={14} className="animate-spin text-primary shrink-0" />
          )}
          <span className="tracking-tight">{captionText}</span>
        </div>
      </div>

      {/* Collapsible Details Panel overlay if toggled */}
      {showDetails && (
        <div className="mx-8 mb-2 h-44 shrink-0 overflow-hidden">
          <StageList bootstrap={bootstrap} />
        </div>
      )}

      {/* Region C: Bottom Toolbar */}
      <div className="flex shrink-0 items-center justify-between border-t border-border px-8 py-3 bg-card/40 backdrop-blur-xs">
        <button
          type="button"
          onClick={() => setShowDetails((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <FileText size={14} />
          {showDetails ? '隐藏详情' : '显示详情'}
          <ChevronRight
            size={12}
            className={clsx('transition-transform duration-200', showDetails && 'rotate-90')}
          />
        </button>

        {bootstrap.status === 'running' && (
          <Button variant="outline" size="sm" onClick={() => void cancelInstall()}>
            取消安装
          </Button>
        )}
      </div>
    </div>
  )
}
