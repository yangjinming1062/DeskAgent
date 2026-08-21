import { Component, type ErrorInfo, type ReactNode } from 'react'

import { log } from '@/shared/lib/log'
import { strings } from '@/shared/strings'

import { Button, ErrorState } from './ui'

export interface ErrorBoundaryFallbackProps {
  error: Error
  reset: () => void
}

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: (props: ErrorBoundaryFallbackProps) => ReactNode
  label?: string
  onError?: (error: Error, info: ErrorInfo) => void
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    log.error(this.props.label ? `error-boundary:${this.props.label}` : 'error-boundary', error, info.componentStack)
    this.props.onError?.(error, info)
  }

  reset = () => {
    this.setState({ error: null })
  }

  override render() {
    const { error } = this.state

    if (!error) {
      return this.props.children
    }

    if (this.props.fallback) {
      return this.props.fallback({ error, reset: this.reset })
    }

    return <RootErrorFallback error={error} reset={this.reset} />
  }
}

function RootErrorFallback({ error, reset }: ErrorBoundaryFallbackProps): React.JSX.Element {
  const t = strings

  return (
    <div className="fixed inset-0 z-[1500] grid place-items-center bg-(--ui-chat-surface-background) p-6">
      <ErrorState
        className="w-full max-w-[28rem]"
        description={error.message || t.errors.boundaryDesc}
        title={t.errors.boundaryTitle}
      >
        <Button className="font-semibold" onClick={reset} size="lg">
          {t.common.retry}
        </Button>
        <Button onClick={() => window.location.reload()} variant="text">
          {t.errors.reloadWindow}
        </Button>
      </ErrorState>
    </div>
  )
}
