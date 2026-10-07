import { useStore } from '@nanostores/react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { findGroupOfPane, group, split } from '@/components/pane-shell/tree/model'
import { TreeSplit } from '@/components/pane-shell/tree/renderer/tree-split'
import { $hiddenStripTabs, $hiddenTreePanes, $layoutTree, $narrowViewport } from '@/components/pane-shell/tree/store'
import { $workspaceMode } from '@/components/pane-shell/workspace-scope'
import { registry } from '@/contrib/registry'
import { $panesFlipped, setFileBrowserOpen, setSidebarOpen } from '@/store/layout'
import { stubMenuDomApis, stubResizeObserver } from '@/test/jsdom'

import { SessionsTabTitle } from './sessions-tab-title'
import { TitlebarControls } from './titlebar-controls'
vi.mock('@/store/session-dot-state', async () => {
  const { atom } = await import('nanostores')

  return { $unreadSessionCount: atom(3) }
})
const disposers: Array<() => void> = []
afterEach(() => {
  cleanup()
  disposers.splice(0).forEach(f => f())
})

function VisibleSessions() {
  const tree = useStore($layoutTree)

  return tree?.type === 'split' ? <TreeSplit node={tree} root rootRow /> : null
}

it.each(['left', 'right'])('keeps one unread count when %s Sessions collapses to its visible restore rail', side => {
  stubResizeObserver()
  stubMenuDomApis()
  $workspaceMode.set('sessions')
  $panesFlipped.set(false)
  $narrowViewport.set(false)
  $hiddenStripTabs.set(new Set())
  $hiddenTreePanes.set(new Set())
  setSidebarOpen(true)
  setFileBrowserOpen(true)

  for (const [id, placement] of Object.entries({ sessions: 'left', bots: 'left', workspace: 'main', files: 'right' })) {
    disposers.push(
      registry.register({
        area: 'panes',
        id,
        title: id,
        data: {
          placement,
          ...(id === 'sessions'
            ? { hideOnly: true, tabTitle: () => <SessionsTabTitle onOpenNextUnread={vi.fn()} unread={3} /> }
            : {})
        },
        render: () => null
      })
    )
  }

  const sessions = group(['sessions', 'bots'], { id: 'sidebar', active: 'sessions' }),
    main = group(['workspace']),
    files = group(['files'])

  $layoutTree.set(split('row', side === 'left' ? [sessions, main, files] : [files, main, sessions]))
  render(
    <MemoryRouter>
      <TitlebarControls onOpenSettings={vi.fn()} />
      <VisibleSessions />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('button', { name: /3 unread sessions/ })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: side === 'left' ? 'Hide sidebar' : 'Hide right sidebar' }))
  expect(findGroupOfPane($layoutTree.get()!, 'sessions')!.minimized).toBe(true)
  expect(screen.getAllByRole('button', { name: /3 unread sessions/ })).toHaveLength(1)
  const unchangedTree = $layoutTree.get()
  const setSide = side === 'left' ? setSidebarOpen : setFileBrowserOpen
  act(() => setSide(false))
  expect($layoutTree.get()).toBe(unchangedTree)
  expect(screen.getAllByRole('button', { name: /3 unread sessions/ })).toHaveLength(1)
  expect(screen.getByRole('button', { name: /3 unread sessions/ }).getAttribute('aria-label')).toMatch(/Show/i)
  act(() => setSide(true))
  expect(screen.getAllByRole('button', { name: /3 unread sessions/ })).toHaveLength(1)
})
