import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { findGroupOfPane, group, split } from '@/components/pane-shell/tree/model'
import { $hiddenStripTabs, $layoutTree } from '@/components/pane-shell/tree/store'
import { $workspaceMode } from '@/components/pane-shell/workspace-scope'
import { registry } from '@/contrib/registry'
import { $panesFlipped, setFileBrowserOpen, setSidebarOpen } from '@/store/layout'

import { TitlebarControls } from './titlebar-controls'

vi.mock('@/store/session-dot-state', async () => {
  const { atom } = await import('nanostores')

  return { $unreadSessionCount: atom(3) }
})

const renderControls = () =>
  render(
    <MemoryRouter>
      <TitlebarControls onOpenSettings={vi.fn()} />
    </MemoryRouter>
  )

beforeAll(() => {
  const disposers = Object.entries({
    sessions: 'left',
    bots: 'left',
    terminal: 'left',
    files: 'right',
    workspace: 'main'
  }).map(([id, placement]) =>
    registry.register({ id, area: 'panes', title: id, data: { placement }, render: () => null })
  )

  return () => disposers.forEach(dispose => dispose())
})

beforeEach(() => {
  $workspaceMode.set('sessions')
  $panesFlipped.set(false)
  setSidebarOpen(true)
  setFileBrowserOpen(true)
  $hiddenStripTabs.set(new Set())
  $layoutTree.set(split('row', [group(['sessions', 'bots', 'terminal']), group(['workspace'])]))
})

afterEach(() => {
  cleanup()
})

describe('rendered unread badge ownership', () => {
  it('keeps the count on Sessions after dragging it to the other side without flipping the layout', () => {
    setFileBrowserOpen(false)
    $layoutTree.set(split('row', [group(['files']), group(['workspace']), group(['sessions'])]))
    renderControls()
    expect(screen.getAllByRole('button', { name: /3 unread sessions/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: /3 unread sessions/ }).getAttribute('aria-label')).toMatch(/right/i)
  })

  it('does not attach a count to the visible sidebar hide button', () => {
    renderControls()

    expect(screen.queryByRole('button', { name: /3 unread sessions/ })).toBeNull()
  })

  it('does not label a Bots workspace toggle with a Sessions count', () => {
    $workspaceMode.set('bots')
    setSidebarOpen(false)
    renderControls()

    expect(screen.queryByRole('button', { name: /3 unread sessions/ })).toBeNull()
  })

  it('does not label a Terminal reveal control with a Sessions count', () => {
    $layoutTree.set(split('row', [group(['sessions', 'terminal'], { active: 'terminal' }), group(['workspace'])]))
    setSidebarOpen(false)
    renderControls()

    expect(screen.queryByRole('button', { name: /3 unread sessions/ })).toBeNull()
  })

  it.each([false, true])('puts one count on the hidden Sessions reveal control (flipped=%s)', flipped => {
    $panesFlipped.set(flipped)

    if (flipped) {
      $layoutTree.set(split('row', [group(['files']), group(['workspace']), group(['sessions'])]))
    }

    setSidebarOpen(false)
    setFileBrowserOpen(true)
    renderControls()

    const controls = screen.getAllByRole('button', { name: /3 unread sessions/ })

    expect(controls).toHaveLength(1)
    expect(controls[0].textContent).toContain('3')
    expect(controls[0].getAttribute('aria-label')).toMatch(/show/i)
  })
})

it.each(['dragged', 'flipped'])('tracks actual right Sessions visibility through a button press: %s', arrangement => {
  const flipped = arrangement === 'flipped'
  $panesFlipped.set(flipped)
  setSidebarOpen(true)
  setFileBrowserOpen(!flipped)
  $layoutTree.set(split('row', [group(['files']), group(['workspace']), group(['sessions'])]))
  renderControls()
  // Sessions is physically right, unminimized and uncollapsed. Files preference is independent.
  expect(screen.queryByRole('button', { name: /3 unread sessions/ })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Hide right sidebar/i }))
  expect(findGroupOfPane($layoutTree.get()!, 'sessions')?.minimized).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: /Show right sidebar/i }))
  expect(findGroupOfPane($layoutTree.get()!, 'sessions')?.minimized).toBeFalsy()
  expect(screen.queryByRole('button', { name: /3 unread sessions/ })).toBeNull()
})
