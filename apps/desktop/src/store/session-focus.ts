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

/** Session identity belongs to the same content-focus derivation; legacy callers re-export it. */
export const $focusedSessionIsTile = computed($focusedTreePaneId, active =>
  Boolean(active?.startsWith(TILE_PANE_PREFIX))
)

export const $focusedStoredSessionId = computed([$focusedTreePaneId, $selectedStoredSessionId], (active, selected) => {
  if (active?.startsWith(TILE_PANE_PREFIX)) {
    return active.slice(TILE_PANE_PREFIX.length)
  }

  // A contributed workspace tab is not the route-driven primary chat. The
  // primary selection remains cached behind it, but must not hold session
  // focus: returning to a retained Bot Chat tile needs a fresh focus edge
  // (and a transcript refresh before acknowledging its unread marker).
  if (active?.startsWith('plugin-workspace:')) {
    return null
  }

  return selected
})
