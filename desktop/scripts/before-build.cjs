/**
 * Desktop bundles ship precompiled renderer assets. Returning false here tells
 * electron-builder to skip the node_modules collector/install step, which
 * avoids workspace dependency graph explosions and keeps packaging
 * deterministic across environments. The desktop does not bundle the DeskAgent
 * Agent Python payload nor drive any install / update / uninstall flow —
 * that is owned by the installer module's Tauri `DeskAgent-Setup` binary (see
 * installer/CLAUDE.md).
 */
module.exports = async function beforeBuild() {
  return false
}
