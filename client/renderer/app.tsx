import React, { Suspense } from 'react'

import { CompanionRoot } from '@/companion'
import { ToolRoot } from '@/hub'

const ClipDebugger = React.lazy(async () => {
  const mod = await import('@/clip-debugger/clip-debugger')

  return { default: mod.ClipDebugger }
})

function readRole(): 'clip' | 'sprite' | 'tool' {
  const params = new URLSearchParams(window.location.search)
  const role = params.get('role')

  if (role === 'sprite') {
    return 'sprite'
  }

  if (role === 'clip' || role === 'anim' || role === 'animation') {
    return 'clip'
  }

  if (window.location.hash.startsWith('#/clip') || window.location.hash.startsWith('#/anim')) {
    return 'clip'
  }

  return 'tool'
}

export default function App(): React.JSX.Element {
  const role = readRole()

  if (role === 'sprite') {
    return <CompanionRoot />
  }

  if (role === 'clip') {
    return (
      <Suspense fallback={<div className="h-screen w-screen bg-[#0d0d0d]" />}>
        <ClipDebugger />
      </Suspense>
    )
  }

  return <ToolRoot />
}
