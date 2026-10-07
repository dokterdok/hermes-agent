import { atom, computed } from 'nanostores'

import { findGroup, findGroupOfPane } from '@/components/pane-shell/tree/model'
import { $activeTreeGroup, $layoutTree } from '@/components/pane-shell/tree/store'
import { $workspaceMode } from '@/components/pane-shell/workspace-scope'

import { $selectedStoredSessionId } from './session'

// Content: the primary chat, a session tile, or a contributed workspace such
// as a group chat. Files, Terminal, previews and the sessions list are chrome.
export const TILE_PANE_PREFIX = 'session-tile:'

const isContentPane = (paneId?: string): boolean =>
  paneId === 'workspace' || Boolean(paneId?.startsWith(TILE_PANE_PREFIX) || paneId?.startsWith('plugin-workspace:'))

// Chrome can own keyboard focus, but working in it (navigating the sessions
// list, browsing Files, typing in Terminal) must not replace the chat being
// worked in with the route's (possibly hidden) primary — the Files rail and
// statusbar follow this chat, so they would jump projects mid-click.
// Remember the pane: a preview can replace its group's active chat tab.
const $lastContentPane = atom<null | string>(null)

const rememberContentPane = () => {
  const groupId = $activeTreeGroup.get()
  const tree = $layoutTree.get()
  const active = groupId && tree ? findGroup(tree, groupId)?.active : undefined

  if (!groupId || isContentPane(active)) {
    $lastContentPane.set(active ?? null)

    return
  }

  // Chrome owns focus: follow the remembered group when it fronts another
  // chat (⌘1..9, drag-to-split) so a preview covering it later keeps it.
  const last = $lastContentPane.get()
  const content = last && tree ? findGroupOfPane(tree, last) : null

  if (content && isContentPane(content.active)) {
    $lastContentPane.set(content.active)
  }
}

$activeTreeGroup.subscribe(rememberContentPane)
$layoutTree.listen(rememberContentPane)

export const $focusedTreePaneId = computed(
  [$activeTreeGroup, $layoutTree, $workspaceMode, $lastContentPane],
  (groupId, tree, workspaceMode, lastContentPane) => {
    let active = groupId && tree ? findGroup(tree, groupId)?.active : undefined

    if (groupId && tree && !isContentPane(active)) {
      // Keep the remembered chat while chrome or a preview covers it.
      active =
        lastContentPane && findGroupOfPane(tree, lastContentPane)
          ? lastContentPane
          : findGroupOfPane(tree, 'workspace')?.active
    }

    if (active?.startsWith(TILE_PANE_PREFIX) || active?.startsWith('plugin-workspace:')) {
      return active
    }

    // Bot chats are tiles, never the primary selection. Sidebar roster focus
    // must not publish a null session and let the Bots home reclaim the chat.
    if (workspaceMode === 'bots' && tree) {
      const mainActive = findGroupOfPane(tree, 'workspace')?.active

      if (mainActive?.startsWith(TILE_PANE_PREFIX)) {
        return mainActive
      }
    }

    return active
  }
)

/** The stored id of the session the user is working in: a focused
 *  `session-tile:<storedId>` pane IS that session, anything else falls back to
 *  the route-driven primary selection.
 *
 *  Lives HERE, not in session-states.ts, because low-level stores (the preview
 *  rail) need it and session-states imports them — defining it there made
 *  `session-states` ⇄ `preview` a load cycle. The inputs (the layout tree and
 *  the primary selection) are both leaf stores, so every consumer can share one
 *  derivation without dragging session-states in. */
export const $focusedSessionIsTile = computed($focusedTreePaneId, active =>
  Boolean(active?.startsWith(TILE_PANE_PREFIX))
)

export const $focusedStoredSessionId = computed([$focusedTreePaneId, $selectedStoredSessionId], (active, selected) => {
  if (active?.startsWith(TILE_PANE_PREFIX)) {
    return active.slice(TILE_PANE_PREFIX.length)
  }

  // A contributed workspace has its own conversation; a cached primary or
  // Bot Chat behind it is not being read.
  return active?.startsWith('plugin-workspace:') ? null : selected
})
