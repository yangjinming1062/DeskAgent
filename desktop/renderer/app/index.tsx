import { CompanionRoot } from '@/companion/root'
import { ToolRoot } from './tool-root'

// The shared renderer bundle branches at the root on a `?role=` query param
// stamped by main (rendererUrlFor): the transparent sprite window runs
// CompanionRoot, the framed tool window runs ToolRoot (Login / Settings).
function readRole(): 'sprite' | 'tool' {
  return new URLSearchParams(window.location.search).get('role') === 'sprite' ? 'sprite' : 'tool'
}

export default function App() {
  return readRole() === 'sprite' ? <CompanionRoot /> : <ToolRoot />
}
