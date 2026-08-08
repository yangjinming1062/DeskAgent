import type React from 'react'

import { CompanionRoot } from '@/companion'
import { ToolRoot } from '@/hub'

function readRole(): 'sprite' | 'tool' {
  return new URLSearchParams(window.location.search).get('role') === 'sprite' ? 'sprite' : 'tool'
}

export default function App(): React.JSX.Element {
  return readRole() === 'sprite' ? <CompanionRoot /> : <ToolRoot />
}
