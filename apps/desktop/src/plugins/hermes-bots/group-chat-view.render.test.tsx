import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import type * as groupRoundsModule from './group-rounds'
const { stopGroupThread } = vi.hoisted(() => ({ stopGroupThread: vi.fn(async () => undefined) }))

import { translateBots } from './i18n-test-helper'

// Room bodies go through the shell's message renderer (the 1:1 chat's code
// card + `MEDIA:` transform) when the SDK exports it. The stub records what the
// room handed it so the test asserts the wiring, not the renderer's output.
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock, createGroupGateway } = await import('./group-test-utils')
  const base = await pluginSdkMock(createGroupGateway().host)
  const { useStore } = await import('@nanostores/react')

  const Button = ({ children, onClick, title }: { children?: ReactNode; onClick?: () => void; title?: string }) => (
    <button onClick={onClick} title={title}>
      {children}
    </button>
  )

  return {
    ...base,
    useValue: useStore,
    Button,
    RowButton: Button,
    cn: (...values: unknown[]) => values.filter(Boolean).join(' '),
    Codicon: () => null,
    CopyButton: () => null,
    ConfirmDialog: () => null,
    Dialog: () => null,
    DialogContent: () => null,
    DialogDescription: () => null,
    DialogFooter: () => null,
    DialogHeader: () => null,
    DialogTitle: () => null,
    Input: () => null,
    MessageTextContent: ({ media = true, text }: { media?: boolean; text: string }) => (
      <span data-media={String(media)} data-testid="message-text-content">
        {text}
      </span>
    ),
    ToggleRow: () => null,
    Tip: ({ children }: { children: ReactNode }) => children,
    relativeTime: () => 'now',
    useI18n: () => ({ t: { common: { cancel: 'Cancel', save: 'Save' } } }),
    usePluginI18n: () => translateBots
  }
})
vi.mock('./avatar', () => ({ avatarColor: () => '#888', botAppearance: () => ({}), BotFace: () => null }))
vi.mock('./group-chat-parts', () => ({
  GroupClarifyCard: () => null,
  GroupImageControls: () => null,
  GroupMentionInput: () => null
}))
vi.mock('./group-rounds', async importOriginal => ({
  ...(await importOriginal<typeof groupRoundsModule>()),
  stopGroupThread
}))
// Module transformation is fixture setup, not a render deadline.
beforeAll(async () => {
  await import('./group-chat-view')
}, 30_000)
afterEach(cleanup)

it('renders member replies through the shell message renderer, resolving media only for members on this gateway', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  const { $groupChats } = await import('./group-chat')
  const { GroupChatWorkspace } = await import('./group-chat-view')

  const log = [
    { id: 'u1', thread: 'a', from: { kind: 'user' as const, name: 'You' }, text: 'Show me', at: 1 },
    { id: 'm1', thread: 'a', from: { kind: 'member' as const, name: 'builder' }, text: 'MEDIA:/tmp/local.png', at: 2 },
    {
      id: 'm2',
      thread: 'a',
      from: { kind: 'member' as const, name: 'builder', source: 'mini' },
      text: 'MEDIA:/tmp/remote.png',
      at: 3
    }
  ]

  const members = [
    { name: 'builder' },
    { connectionId: 'mini', connectionLabel: 'mini', name: 'builder', remoteSource: true, sourceScoped: true }
  ] as never

  $groupChats.set({ Room: { log, watermarks: {}, sessions: {} } })
  const { getAllByTestId } = render(<GroupChatWorkspace group="Room" members={members} />)
  // The room paints once the async group-driver gate resolves to the legacy workspace.
  await waitFor(() => expect(getAllByTestId('message-text-content')).toHaveLength(3))
  const bodies = getAllByTestId('message-text-content').map(el => [el.textContent, el.dataset.media])

  expect(bodies).toEqual([
    ['Show me', 'true'],
    ['MEDIA:/tmp/local.png', 'true'],
    ['MEDIA:/tmp/remote.png', 'false']
  ])
})

it('removes Stop controls from historical working rows after the room settles', async () => {
  Element.prototype.scrollIntoView = vi.fn()

  const [{ $groupChats }, activity, { GroupChatWorkspace }] = await Promise.all([
    import('./group-chat'),
    import('./group-activity'),
    import('./group-chat-view')
  ])

  $groupChats.set({
    Settled: {
      epoch: 1,
      log: [],
      members: [{ name: 'builder' }],
      running: false,
      sessions: {},
      watermarks: {}
    }
  })
  activity.recordGroupActivity('Settled', { kind: 'working', member: 'builder' })
  activity.recordGroupActivity('Settled', { kind: 'replied', member: 'builder' })
  activity.recordGroupActivity('Settled', { kind: 'settled', member: null })

  render(<GroupChatWorkspace group="Settled" members={[{ name: 'builder' }]} />)
  // The room paints once the async group-driver gate resolves to the legacy workspace.
  await waitFor(() => expect(screen.getByRole('button', { name: /^Activity/ })).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: /^Activity/ }))

  expect(screen.getByText('builder is working…')).toBeTruthy()
  expect(screen.getByText('builder replied')).toBeTruthy()
  expect(screen.getByText('turn settled')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
})

it('does not pass the latest display activity thread as Stop authority', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  stopGroupThread.mockClear()
  const { $groupChats } = await import('./group-chat')
  const { $groupActivity } = await import('./group-activity')
  const { GroupChatWorkspace } = await import('./group-chat-view')
  const members = [{ name: 'reviewer', title: 'Reviewer' }]

  $groupChats.set({
    Room: {
      epoch: 4,
      log: [
        { id: 'a', thread: 'thread-a', from: { kind: 'user', name: 'You' }, text: 'Active', at: 1 },
        { id: 'b', thread: 'thread-b', from: { kind: 'user', name: 'You' }, text: 'Queued', at: 2 }
      ],
      members,
      running: true,
      sessions: {},
      watermarks: {}
    }
  })
  $groupActivity.set({
    Room: {
      events: [
        { at: 1, epoch: 4, kind: 'working', member: 'reviewer', thread: 'thread-a' },
        { at: 2, epoch: 4, kind: 'queued', member: 'You', thread: 'thread-b' }
      ]
    }
  })
  const { getByRole } = render(<GroupChatWorkspace group="Room" members={members} />)

  await waitFor(() => expect(getByRole('button', { name: 'Stop' })).toBeTruthy())
  fireEvent.click(getByRole('button', { name: 'Stop' }))
  await waitFor(() => expect(stopGroupThread).toHaveBeenCalledTimes(1))
  expect(stopGroupThread).toHaveBeenCalledWith('Room', null, expect.any(Array))
})

it('keeps volatile reconnect cleanup visible until its recovery owner completes', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  const { $groupChats } = await import('./group-chat')
  const { $hostedRoomVolatileCleanup } = await import('./hosted-room-cleanup')
  const { GroupChatWorkspace } = await import('./group-chat-view')
  $groupChats.set({ Pending: { roomId: 'pending-room', log: [], sessions: {}, watermarks: {} } })
  $hostedRoomVolatileCleanup.set([{ setupId: 'reconnect', roomId: 'pending-room', durability: 'volatile' }])
  render(<GroupChatWorkspace group="Pending" members={[]} />)
  await waitFor(() => expect(screen.getByText(/cleanup is pending in this window only/)).toBeTruthy())
  act(() => $hostedRoomVolatileCleanup.set([]))
  await waitFor(() => expect(screen.queryByText(/cleanup is pending in this window only/)).toBeNull())
})
