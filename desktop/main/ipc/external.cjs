'use strict'

// All channels that lived here (deskagent:openExternal) have been removed —
// no renderer callers. `openExternalUrl` is still invoked directly from
// entry.cjs for shell-link / context-menu navigation. Kept as a no-op so the
// registration call site in entry.cjs does not need to feature-flag.
function registerExternalIpc() {}

module.exports = { registerExternalIpc }
