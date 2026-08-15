import path from 'node:path'

export function venvPythonFor(deskagentHome: string, platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32'
    ? path.join(deskagentHome, 'runner', '.venv', 'Scripts', 'python.exe')
    : path.join(deskagentHome, 'runner', '.venv', 'bin', 'python')
}

export interface ResolveVenvPythonOptions {
  deskagentHome?: null | string
  fileExists?: (p: string) => boolean
  platform?: NodeJS.Platform
}

export function resolveVenvPython(
  opts: ResolveVenvPythonOptions = {}
): null | { args: string[]; command: string; kind: string } {
  const { deskagentHome, fileExists, platform } = opts

  if (!deskagentHome || typeof fileExists !== 'function') {
    return null
  }

  const venvPython = venvPythonFor(deskagentHome, platform)
  const serverPy = path.join(deskagentHome, 'runner', 'server.py')

  if (fileExists(venvPython) && fileExists(serverPy)) {
    return { args: [serverPy], command: venvPython, kind: 'venv-python' }
  }

  return null
}
