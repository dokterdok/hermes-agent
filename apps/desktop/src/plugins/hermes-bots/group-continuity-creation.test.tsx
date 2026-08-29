import type * as HermesSdk from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as DataModule from './data'
import type { HostedRoomProbe } from './hosted-room-runtime'
import { translateBots } from './i18n-test-helper'
import type { RosterRow } from './types'

const mocks = vi.hoisted(() => ({
  createAutonomousHostedGroupChat: vi.fn(),
  notify: vi.fn(),
  probeHostedRoomMembers: vi.fn(),
  saveBotMeta: vi.fn(async () => undefined)
}))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const original = await importOriginal<typeof HermesSdk>()

  return {
    ...original,
    host: {
      ...original.host,
      notify: mocks.notify
    },
    usePluginI18n: () => translateBots
  }
})

vi.mock('./data', async importOriginal => {
  const original = await importOriginal<typeof DataModule>()

  return {
    ...original,
    saveBotMeta: mocks.saveBotMeta
  }
})

vi.mock('./hosted-room-runtime', () => ({
  createAutonomousHostedGroupChat: mocks.createAutonomousHostedGroupChat,
  describeAutonomousRoomPlan: () => ({ description: 'Automatic continuity', title: 'Continues' }),
  describeHostedRoomCreationError: () => null,
  probeHostedRoomMembers: mocks.probeHostedRoomMembers
}))

const roster: RosterRow[] = [
  {
    connectionId: 'host-a',
    connectionLabel: 'Studio',
    name: 'research',
    remoteSource: true,
    sourceScoped: true,
    targetProfile: 'research'
  },
  {
    connectionId: 'host-a',
    connectionLabel: 'Studio',
    name: 'builder',
    remoteSource: true,
    sourceScoped: true,
    targetProfile: 'builder'
  }
]

const eligibleProbe: HostedRoomProbe = {
  eligible: true,
  capability: {
    authorityId: 'install:studio',
    connectionId: 'host-a',
    kind: 'driver-capable',
    limits: {
      attachments: false,
      automaticFailover: false,
      crossGatewayMembers: false,
      stagedAttachmentManifest: true
    },
    persistentProcess: true,
    reason: null,
    roomLink: null
  },
  capabilities: {},
  route: {
    connectionId: 'host-a',
    homeConnectionId: 'host-a',
    kind: 'single-gateway',
    limits: {
      attachments: false,
      automaticFailover: false,
      crossGatewayMembers: false,
      stagedAttachmentManifest: true
    },
    memberConnectionIds: ['host-a', 'host-a'],
    remoteConnectionIds: [],
    reason: null
  },
  routes: {}
}

eligibleProbe.capabilities['host-a'] = eligibleProbe.capability!

beforeAll(() => {
  Element.prototype.scrollIntoView = () => undefined
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = () => undefined
  Element.prototype.setPointerCapture = () => undefined
})

beforeEach(async () => {
  vi.clearAllMocks()
  mocks.probeHostedRoomMembers.mockResolvedValue(eligibleProbe)
  mocks.createAutonomousHostedGroupChat.mockResolvedValue({
    authorityId: 'install:studio',
    authorityEpoch: 1,
    connectionId: 'host-a',
    continuityMode: 'gateway'
  })

  const { $groupChats } = await import('./group-chat')

  $groupChats.set({})
})

afterEach(() => {
  cleanup()
})

async function renderSelectedGroup() {
  const { CreateGroupChatDialog } = await import('./create-dialog')

  const Harness = () => {
    const [open, setOpen] = useState(true)

    return <CreateGroupChatDialog onClose={() => setOpen(false)} open={open} roster={roster} />
  }

  render(<Harness />)

  const checkboxes = screen.getAllByRole('checkbox')

  fireEvent.click(checkboxes[0])
  fireEvent.click(checkboxes[1])

  await waitFor(() => expect(mocks.probeHostedRoomMembers).toHaveBeenCalledTimes(1))

  return screen.getByRole('button', {
    name: 'Create Group (2)'
  }) as HTMLButtonElement
}

describe('automatic Group Chat continuity', () => {
  it('has no creation switch and selects hosted continuity automatically when eligible', async () => {
    const create = await renderSelectedGroup()

    expect(screen.queryByRole('switch')).toBeNull()
    await waitFor(() => expect(create.disabled).toBe(false))
    fireEvent.click(create)

    await waitFor(() => expect(mocks.createAutonomousHostedGroupChat).toHaveBeenCalledTimes(1))

    const { $groupChats } = await import('./group-chat')
    const created = Object.values($groupChats.get())[0]

    expect(created).toMatchObject({
      continuityMode: 'gateway',
      hosted: 'install:studio',
      hostedConnectionId: 'host-a',
      hostedEpoch: 1
    })
    expect(mocks.notify).not.toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining('pause when Desktop closes')
      })
    )
  })

  it('keeps Create disabled until the probe settles', async () => {
    let settleProbe: (probe: HostedRoomProbe) => void = () => undefined

    mocks.probeHostedRoomMembers.mockImplementation(
      () =>
        new Promise(resolve => {
          settleProbe = resolve
        })
    )

    const create = await renderSelectedGroup()

    expect(create.disabled).toBe(true)
    settleProbe(eligibleProbe)
    await waitFor(() => expect(create.disabled).toBe(false))
  })

  it('creates a classic Desktop Group Chat when the probe fails', async () => {
    mocks.probeHostedRoomMembers.mockRejectedValue(new Error('offline'))
    const create = await renderSelectedGroup()

    await waitFor(() => expect(create.disabled).toBe(false))
    fireEvent.click(create)

    const { $groupChats } = await import('./group-chat')

    await waitFor(() => expect(Object.values($groupChats.get())).toHaveLength(1))
    const created = Object.values($groupChats.get())[0]

    expect(created.continuityMode).toBe('desktop')
    expect(created.hosted ?? null).toBeNull()
    expect(mocks.createAutonomousHostedGroupChat).not.toHaveBeenCalled()
  })

  it('falls back to Desktop and shows one concise notice when hosted creation fails', async () => {
    mocks.createAutonomousHostedGroupChat.mockRejectedValue(new Error('host refused'))
    const create = await renderSelectedGroup()

    await waitFor(() => expect(create.disabled).toBe(false))
    fireEvent.click(create)

    const { $groupChats } = await import('./group-chat')

    await waitFor(() => expect(Object.values($groupChats.get())).toHaveLength(1))
    const created = Object.values($groupChats.get())[0]

    expect(created.continuityMode).toBe('desktop')
    expect(created.hosted ?? null).toBeNull()

    const fallback = mocks.notify.mock.calls.filter(([payload]) =>
      String(payload?.message || '').includes('Bots will pause when Desktop closes')
    )

    expect(fallback).toHaveLength(1)
    expect(fallback[0][0]).toEqual(
      expect.objectContaining({
        kind: 'info',
        message: 'Studio could not keep this Group Chat running. Bots will pause when Desktop closes.'
      })
    )
  })

  it('does not create a competing Desktop room while distributed cleanup is pending', async () => {
    mocks.createAutonomousHostedGroupChat.mockRejectedValue(
      Object.assign(new Error('cleanup pending'), {
        fallbackSafe: false
      })
    )
    const create = await renderSelectedGroup()

    await waitFor(() => expect(create.disabled).toBe(false))
    fireEvent.click(create)

    const { $groupChats } = await import('./group-chat')

    await waitFor(() => expect(screen.getByText('Could not create the Group Chat. Try again.')).toBeTruthy())
    expect(Object.values($groupChats.get())).toHaveLength(0)
  })
})
