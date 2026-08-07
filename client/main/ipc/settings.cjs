'use strict'

// All deskagent:setting:defaultProjectDir:* channels had zero renderer
// callers and were removed. Kept as a no-op so the registration call site in
// entry.cjs does not need to feature-flag.
function registerSettingsIpc() {}

module.exports = { registerSettingsIpc }
