import { atom, computed } from 'nanostores'

export interface ContextMenuPos {
  x: number
  y: number
}

// Persistent position so the menu can stay mounted and just toggle visibility.
// Kept in a nanostore (not useState) so opening/closing doesn't re-render the
// heavy CompanionRoot (8 useStore + 7 useState).
export const $contextMenuPos = atom<ContextMenuPos | null>(null)

// Derived atom — consumed by companions that want to skip canvas work while
// the menu is open (e.g. `companion-3d.tsx` gates `pointermove` look-at here).
export const $contextMenuOpen = computed($contextMenuPos, pos => pos !== null)

export function openContextMenu(pos: ContextMenuPos): void {
  $contextMenuPos.set(pos)
}

export function closeContextMenu(): void {
  $contextMenuPos.set(null)
}
