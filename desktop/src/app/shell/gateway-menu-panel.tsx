import { StatusDot } from '@/components/status-dot'
import { useI18n } from '@/i18n'
import { Activity, AlertCircle } from '@/lib/icons'
import type { RuntimeReadinessResult } from '@/lib/runtime-readiness'
import { cn } from '@/lib/utils'
import type { StatusResponse } from '@/types/zast'

interface GatewayMenuPanelProps {
  gatewayState: string
  inferenceStatus: RuntimeReadinessResult | null
  statusSnapshot: StatusResponse | null
}

export function GatewayMenuPanel({
  gatewayState,
  inferenceStatus,
  statusSnapshot: _statusSnapshot
}: GatewayMenuPanelProps) {
  const { t } = useI18n()
  const copy = t.shell.gatewayMenu
  const gatewayOpen = gatewayState === 'open'
  const gatewayConnecting = gatewayState === 'connecting'
  const inferenceReady = gatewayOpen && inferenceStatus?.ready === true

  const connectionLabel = gatewayOpen
    ? copy.connected
    : gatewayConnecting
      ? copy.connecting
      : (gatewayState || copy.offline).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

  const inferenceLabel = gatewayOpen
    ? inferenceStatus?.ready
      ? copy.inferenceReady
      : inferenceStatus
        ? copy.inferenceNotReady
        : copy.checkingInference
    : copy.disconnected

  return (
    <div className="text-sm">
      <div className="flex items-center justify-between gap-2 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          {inferenceReady ? (
            <Activity className="size-3.5 text-primary" />
          ) : (
            <AlertCircle className={cn('size-3.5', gatewayOpen ? 'text-amber-600' : 'text-destructive')} />
          )}
          <span className="font-medium">{copy.gateway}</span>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <StatusDot tone={inferenceReady ? 'good' : gatewayOpen ? 'warn' : 'bad'} />
            {inferenceLabel}
          </span>
        </div>
      </div>

      <div className="border-t border-border/50 px-3 py-2 text-xs text-muted-foreground">
        <div>{copy.connection(connectionLabel)}</div>
        {inferenceStatus?.reason && <div className="mt-1 line-clamp-3">{inferenceStatus.reason}</div>}
      </div>
    </div>
  )
}
