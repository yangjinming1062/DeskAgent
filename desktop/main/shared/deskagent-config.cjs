'use strict'

// Thin re-export shim — the persisted-override chain lives in
// `shared/config.cjs::resolveBackendUrl` so all callers (auth IPC,
// entry.cjs bootstrap, login form prefill, auto-updater feed) resolve
// through one precedence chain instead of two parallel ones. Kept as a
// separate module so the existing `require('./shared/deskagent-config.cjs')`
// sites and tests don't need to be touched.

module.exports = require('./config.cjs')