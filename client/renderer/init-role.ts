// Stamp <html data-role="sprite|tool"> synchronously before <body>
// parses so the sprite window's "background: transparent" rule in
// styles.css applies on the very first paint. Without this, the body
// briefly fills white (browser default), defeating the point of the
// transparent BrowserWindow.
document.documentElement.dataset.role = new URLSearchParams(location.search).get('role') || 'tool'
