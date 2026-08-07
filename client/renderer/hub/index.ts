// Public surface of renderer/hub. Imports outside this barrel go via
// `@/hub/<module>`/<file>` directly. The tool-window entry (app.tsx)
// mounts ToolRoot; Login and Settings are separate pages mounted by the root.

export { LoginPage } from './login/login-page'
export { ToolRoot } from './root'
export { SettingsView } from './settings'

export { $updateStatus, selectTargetVersion } from './settings-store'
export type { SettingsPageProps } from './settings/types'

export type { SettingsView as SettingsViewId } from './settings/types'
