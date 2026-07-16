// The single source of truth for rebindable desktop hotkeys.
//
// Each entry is pure metadata: an id, a category, and the default combo(s).
// Handlers are wired separately in `use-keybinds.ts` (they need React context
// like navigate / theme); labels come from i18n (`t.keybinds.actions[id]`). To
// add a hotkey, add a row here and a handler there — nothing else.

export type KeybindCategory = 'composer' | 'session' | 'navigation' | 'view'

// The self-referential opener — bound + dispatched like any action, but shown in
// the panel subtitle (not as its own row).
export const KEYBIND_PANEL_ACTION = 'keybinds.openPanel'

// `composer` is read-only; the rest are rebindable. `view` is the catch-all for
// layout, appearance, and the panel-opener.
export const KEYBIND_CATEGORIES: readonly KeybindCategory[] = ['composer', 'session', 'navigation', 'view']

export interface KeybindActionMeta {
  id: string
  category: KeybindCategory
  /** Default combos. Empty = shipped unbound (user can assign one). */
  defaults: readonly string[]
}

export const KEYBIND_ACTIONS: readonly KeybindActionMeta[] = [
  // ── Composer ─────────────────────────────────────────────────────────────
  { id: 'composer.focus', category: 'composer', defaults: [] },
  { id: 'composer.modelPicker', category: 'composer', defaults: [] },

  // ── Session ──────────────────────────────────────────────────────────────
  { id: 'session.new', category: 'session', defaults: ['mod+n', 'shift+n'] },
  { id: 'session.next', category: 'session', defaults: [] },
  { id: 'session.prev', category: 'session', defaults: [] },
  { id: 'session.focusSearch', category: 'session', defaults: ['mod+shift+f'] },
  { id: 'session.togglePin', category: 'session', defaults: [] },

  // ── Navigation ───────────────────────────────────────────────────────────
  { id: 'nav.commandPalette', category: 'navigation', defaults: ['mod+k', 'mod+p'] },
  { id: 'nav.settings', category: 'navigation', defaults: ['mod+,'] },
  { id: 'nav.artifacts', category: 'navigation', defaults: [] },
  { id: 'nav.agents', category: 'navigation', defaults: [] },

  // ── View (layout + appearance + the shortcuts panel itself) ───────────────
  { id: 'view.toggleSidebar', category: 'view', defaults: ['mod+b'] },
  { id: 'view.toggleRightSidebar', category: 'view', defaults: ['mod+j'] },
  { id: 'view.showFiles', category: 'view', defaults: [] },
  { id: 'view.showTerminal', category: 'view', defaults: [] },
  // ⌘\ — the backslash reads like a mirror line flipping the layout.
  { id: 'view.flipPanes', category: 'view', defaults: ['mod+\\'] },
  { id: 'appearance.toggleMode', category: 'view', defaults: ['shift+x'] },
  { id: 'keybinds.openPanel', category: 'view', defaults: ['mod+/'] }
]

export const KEYBIND_ACTION_IDS: readonly string[] = KEYBIND_ACTIONS.map(action => action.id)

const ACTION_BY_ID = new Map(KEYBIND_ACTIONS.map(action => [action.id, action]))

export function keybindAction(id: string): KeybindActionMeta | undefined {
  return ACTION_BY_ID.get(id)
}

export type KeybindBindings = Record<string, string[]>

export function defaultBindings(): KeybindBindings {
  return Object.fromEntries(KEYBIND_ACTIONS.map(action => [action.id, [...action.defaults]]))
}

// Fixed, non-rebindable shortcuts surfaced read-only in the panel so the map is
// complete. `keys` are canonical tokens run through `formatCombo` for display
// (single symbols like "@" / "/" pass through unchanged). Categories listed here
// render after the rebindable ones.
export interface KeybindReadonly {
  id: string
  category: KeybindCategory
  keys: readonly string[]
}

export const KEYBIND_READONLY: readonly KeybindReadonly[] = [
  { id: 'composer.send', category: 'composer', keys: ['enter'] },
  { id: 'composer.newline', category: 'composer', keys: ['shift+enter'] },
  { id: 'composer.steer', category: 'composer', keys: ['mod+enter'] },
  { id: 'composer.sendQueued', category: 'composer', keys: ['mod+shift+k'] },
  { id: 'composer.mention', category: 'composer', keys: ['@'] },
  { id: 'composer.slash', category: 'composer', keys: ['/'] },
  { id: 'composer.help', category: 'composer', keys: ['?'] },
  { id: 'composer.history', category: 'composer', keys: ['up', 'down'] },
  { id: 'composer.cancel', category: 'composer', keys: ['escape'] },
  // Fixed, context-local shortcuts surfaced for discoverability.
  { id: 'view.terminalSelection', category: 'view', keys: ['mod+l'] },
  { id: 'view.closePreviewTab', category: 'view', keys: ['mod+w'] }
]
