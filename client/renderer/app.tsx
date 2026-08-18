import type React from 'react'

import { ClipDebugger } from '@/clip-debugger/clip-debugger'
import { CompanionRoot } from '@/companion'
import { ToolRoot } from '@/hub'

function readRole(): 'sprite' | 'tool' | 'clip' {
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
    return <ClipDebugger />
  }

  return <ToolRoot />
}
