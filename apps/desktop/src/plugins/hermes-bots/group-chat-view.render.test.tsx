import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { translateBots } from './i18n-test-helper'

// Room bodies go through the shell's message renderer (the 1:1 chat's code
// card + `MEDIA:` transform) when the SDK exports it. The stub records what the
// room handed it so the test asserts the wiring, not the renderer's output.
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock, createGroupGateway } = await import('./group-test-utils')
  const base = await pluginSdkMock(createGroupGateway().host)

  const Button = ({ children, onClick, title }: { children?: ReactNode; onClick?: () => void; title?: string }) => (
    <button onClick={onClick} title={title}>
      {children}
    </button>
  )

  return {
    ...base,
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
    MessageTextContent: ({ media = true, previewOnly = false, text }: { media?: boolean; previewOnly?: boolean; text: string }) => (
      <span data-media={String(media)} data-preview-only={String(previewOnly)} data-testid="message-text-content">
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
    },
    // A seat no longer in the room, an install that is not this one, a matching install under an
    // old label, and local seats matched by their connection label (as on main; m6's seat has no
    // install id to contradict the row's).
    { id: 'm3', thread: 'a', from: { kind: 'member' as const, name: 'former', source: 'renamed-mini' }, text: 'MEDIA:/tmp/missing-remote.png', at: 4 },
    { id: 'm4', thread: 'a', from: { kind: 'member' as const, name: 'builder', source: 'Studio', gateway: 'foreign-install' }, text: 'MEDIA:/tmp/conflicting-origin.png', at: 5 },
    { id: 'm5', thread: 'a', from: { kind: 'member' as const, name: 'builder', source: 'Old local label', gateway: 'local-install' }, text: 'MEDIA:/tmp/proven-local.png', at: 6 },
    { id: 'm6', thread: 'a', from: { kind: 'member' as const, name: 'legacy', source: 'Studio', gateway: 'foreign-install' }, text: 'MEDIA:/tmp/no-local-identity.png', at: 7 },
    { id: 'm7', thread: 'a', from: { kind: 'member' as const, name: 'legacy', source: 'Studio' }, text: 'MEDIA:/tmp/source-label-only.png', at: 8 },
    // A label naming the remote seat, with this install's id copied in (a mirror writer can do that).
    { id: 'm8', thread: 'a', from: { kind: 'member' as const, name: 'builder', source: 'mini', gateway: 'local-install' }, text: 'MEDIA:/home/user/.ssh/id_rsa', at: 9 }
  ]

  const members = [
    { name: 'builder', connectionId: 'local', connectionLabel: 'Studio', installId: 'local-install' },
    { name: 'legacy', connectionId: 'local', connectionLabel: 'Studio' },
    { connectionId: 'mini', connectionLabel: 'mini', name: 'builder', remoteSource: true, sourceScoped: true }
  ] as never

  $groupChats.set({ Room: { log, watermarks: {}, sessions: {} } })
  const { getAllByTestId } = render(<GroupChatWorkspace group="Room" members={members} />)
  // The room paints once the async group-driver gate resolves to the legacy workspace.
  await waitFor(() => expect(getAllByTestId('message-text-content')).toHaveLength(9))
  const bodies = getAllByTestId('message-text-content').map(el => [el.textContent, el.dataset.media, el.dataset.previewOnly])

  expect(bodies).toEqual([
    ['Show me', 'true', 'false'],
    ['MEDIA:/tmp/local.png', 'true', 'false'],
    ['MEDIA:/tmp/remote.png', 'false', 'true'],
    ['MEDIA:/tmp/missing-remote.png', 'false', 'true'],
    ['MEDIA:/tmp/conflicting-origin.png', 'false', 'true'],
    ['MEDIA:/tmp/proven-local.png', 'true', 'false'],
    ['MEDIA:/tmp/no-local-identity.png', 'true', 'false'],
    ['MEDIA:/tmp/source-label-only.png', 'true', 'false'],
    ['MEDIA:/home/user/.ssh/id_rsa', 'false', 'true']
  ])
})

// No seat sits on the foreground gateway, so nothing in this room was written there: the user's
// rows and unsourced member rows must not resolve MEDIA: paths or file links against it either.
it('keeps every row inert in a room with no Bot on this gateway', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  const { $groupChats } = await import('./group-chat')
  const { GroupChatWorkspace } = await import('./group-chat-view')

  const log = [
    { id: 'u1', thread: 'a', from: { kind: 'user' as const, name: 'You' }, text: 'MEDIA:/tmp/user-path.png', at: 1 },
    { id: 'm1', thread: 'a', from: { kind: 'member' as const, name: 'mini-a' }, text: 'MEDIA:/tmp/unsourced.png', at: 2 },
    { id: 'm2', thread: 'a', from: { kind: 'member' as const, name: 'mini-a', source: 'Mini' }, text: 'MEDIA:/tmp/sourced.png', at: 3 }
  ]

  const members = [{ name: 'mini-a', connectionId: 'mini', connectionLabel: 'Mini', remoteSource: true, sourceScoped: true }] as never

  $groupChats.set({ Elsewhere: { log, watermarks: {}, sessions: {} } })
  const { getAllByTestId } = render(<GroupChatWorkspace group="Elsewhere" members={members} />)
  await waitFor(() => expect(getAllByTestId('message-text-content')).toHaveLength(3))

  expect(getAllByTestId('message-text-content').map(el => [el.textContent, el.dataset.media, el.dataset.previewOnly])).toEqual([
    ['MEDIA:/tmp/user-path.png', 'false', 'true'],
    ['MEDIA:/tmp/unsourced.png', 'false', 'true'],
    ['MEDIA:/tmp/sourced.png', 'false', 'true']
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
