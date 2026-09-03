import { createHash } from 'node:crypto'

import { describe, expect, it, vi } from 'vitest'

import type { GroupChat, GroupMessage } from './types'
import { canonicalUser, CLIENT_ID, EVENT_ID, optimisticUser, USER_TEXT, userRoom } from './user-event-test-fixtures'

vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')

  return pluginSdkMock({})
})

function hashed(value: string) {
  return `user:${createHash('sha256').update(value.trim()).digest('hex')}`
}

async function merge(current: GroupChat, incoming: GroupMessage[]) {
  const chat = await import('./group-chat')
  const snapshot = chat.groupChatSyncSnapshot({ Board: { ...current, log: incoming } })

  return chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, { Board: current }).Board
}

describe('hosted client-key/canonical user event identity', () => {
  it('matches the physical UAT client key using an independent standard hash', () => {
    expect(hashed(CLIENT_ID)).toBe(EVENT_ID)
  })

  it.each([false, true])('heals the existing two-entry JSON cache, reversed=%s', async reverse => {
    const chat = await import('./group-chat')
    const canonical = canonicalUser()
    const pair = reverse ? [canonical, optimisticUser()] : [optimisticUser(), canonical]
    const cold = JSON.parse(JSON.stringify(chat.durableGroupChatRooms({ Board: userRoom(pair) })))
    const projection = chat.groupChatSyncSnapshot(cold)
    const restored = chat.mergeRemoteGroupChatSnapshotIntoRooms(projection, cold).Board

    expect(restored.log).toHaveLength(1)
    expect(restored.log[0]).toMatchObject({ id: EVENT_ID, eventId: EVENT_ID, seq: 6, text: USER_TEXT, thread: 'work' })
    expect(projection.rooms['id:room-1'].log).toHaveLength(1)
  })

  it('heals an old mirror that arrives after canonical replay', async () => {
    const restored = await merge(userRoom([canonicalUser()]), [optimisticUser()])

    expect(restored.log).toHaveLength(1)
    expect(restored.log[0].id).toBe(EVENT_ID)
  })

  it('does not invent authority from two unsequenced mirrors alone', async () => {
    const projected = { ...canonicalUser(), seq: undefined }
    const restored = await merge(userRoom([projected]), [optimisticUser()])

    expect(restored.log).toHaveLength(2)
  })

  it.each(['member', 'other-room', 'wrong-hash'])('refuses %s as user-alias proof', async scenario => {
    const canonical = canonicalUser()

    if (scenario === 'member') {
      canonical.from = { kind: 'member', name: 'Bot' }
    }

    if (scenario === 'other-room') {
      canonical.roomId = 'different-room'
    }

    if (scenario === 'wrong-hash') {
      canonical.id = canonical.eventId = hashed('another-key')
    }

    const restored = await merge(userRoom([canonical]), [optimisticUser()])

    expect(restored.log).toHaveLength(2)
  })

  it('preserves genuine old-server raw-ID and newer hashed-ID canonical events', async () => {
    const chat = await import('./group-chat')
    const old = canonicalUser(CLIENT_ID, 4, { text: 'Old backend event' })
    const current = userRoom([old, canonicalUser(EVENT_ID, 6)])
    const mirror = chat.groupChatSyncSnapshot({ Board: current })
    const restored = chat.mergeRemoteGroupChatSnapshotIntoRooms(mirror, { Board: current }).Board

    expect(restored.log.map(entry => [entry.id, entry.seq])).toEqual([
      [CLIENT_ID, 4],
      [EVENT_ID, 6]
    ])
    expect(restored.log[0].text).toBe('Old backend event')
  })

  it('preserves a raw-ID canonical marker across a compact/cold round trip', async () => {
    const chat = await import('./group-chat')
    const old = canonicalUser(CLIENT_ID, 4)
    const coldMirror = JSON.parse(JSON.stringify(chat.groupChatSyncSnapshot({ Board: userRoom([old]) })))

    const restored = chat.mergeRemoteGroupChatSnapshotIntoRooms(coldMirror, {
      Board: userRoom([canonicalUser()])
    }).Board

    expect(restored.log).toHaveLength(2)
    expect(restored.log.find(entry => entry.id === CLIENT_ID)?.eventId).toBe(CLIENT_ID)
  })

  it('never double-hashes canonical-looking IDs or aliases two sequenced events', async () => {
    const first = canonicalUser(EVENT_ID, 6)
    const second = canonicalUser(hashed(EVENT_ID), 7)
    const current = userRoom([first, second])
    const restored = await merge(current, [optimisticUser({ id: EVENT_ID })])

    expect(restored.log.map(entry => [entry.id, entry.seq])).toEqual([
      [EVENT_ID, 6],
      [hashed(EVENT_ID), 7]
    ])
    const unknown = await merge(userRoom([second]), [optimisticUser({ id: EVENT_ID })])
    expect(unknown.log).toHaveLength(2)
  })

  it.each(['body', 'thread', 'attachments'])('canonical data never inherits a mirror %s extension', async changed => {
    const canonical = canonicalUser(EVENT_ID, 6, {
      text: 'C'.repeat(1200),
      images: [{ attachmentId: 'accepted-file', kind: 'file', name: 'accepted.txt' }]
    })

    const mirror = { ...canonical, seq: undefined }

    if (changed === 'body') {
      mirror.text += ' FORGED'
    }

    if (changed === 'thread') {
      mirror.thread = 'wrong-thread'
    }

    if (changed === 'attachments') {
      mirror.images = [{ attachmentId: 'wrong-file', kind: 'file', name: 'wrong.txt' }]
    }

    const restored = await merge(userRoom([canonical]), [mirror])

    expect(restored.log).toHaveLength(1)
    expect(restored.log[0]).toEqual(canonical)
  })

  it('keeps the global strict matcher and classic distinct-ID behavior unchanged', async () => {
    const chat = await import('./group-chat')
    expect(chat.mergeGroupChatSyncEntries([canonicalUser()], [optimisticUser()])).toHaveLength(2)
    const classic = { ...userRoom([canonicalUser()]), hosted: null }
    expect((await merge(classic, [optimisticUser()])).log).toHaveLength(2)
  })

  it.each(['body', 'thread', 'attachments'])(
    'never grafts a reused raw-key mirror %s onto canonical data',
    async changed => {
      const canonical = canonicalUser(EVENT_ID, 6, { text: 'accepted' })
      const mirror = optimisticUser()

      if (changed === 'body') {
        mirror.text = 'invented extension'
      }

      if (changed === 'thread') {
        mirror.thread = 'different-thread'
      }

      if (changed === 'attachments') {
        mirror.images = [{ kind: 'file', name: 'forged.txt', attachmentId: 'forged' }]
      }

      expect((await merge(userRoom([canonical]), [mirror])).log).toEqual([canonical])
    }
  )

  it('does not infer user authority from a sequence supplied by a display projection', async () => {
    const chat = await import('./group-chat')

    const projection = {
      version: 3,
      rooms: { 'id:room-1': { roomId: 'room-1', hosted: 'install:home', log: [canonicalUser()], name: 'Board' } }
    }

    const restored = chat.mergeRemoteGroupChatSnapshotIntoRooms(projection, {
      Board: userRoom([optimisticUser()])
    }).Board

    expect(restored.log).toHaveLength(2)
    expect(restored.log.every(entry => entry.seq === undefined)).toBe(true)
  })

  it('does not use a room-name fallback as proof that different room IDs agree', async () => {
    const chat = await import('./group-chat')
    const other = chat.groupChatSyncSnapshot({ Board: { ...userRoom([optimisticUser()]), roomId: 'other-room' } })

    expect(
      chat.mergeRemoteGroupChatSnapshotIntoRooms(other, { Board: userRoom([canonicalUser()]) }).Board.log
    ).toHaveLength(2)
  })

  it('retains ambiguous sequenced records without selecting a donor for their mirror', async () => {
    const chat = await import('./group-chat')

    const entries = chat.mergeGroupChatRoomEntries(
      userRoom([]),
      [canonicalUser(EVENT_ID, 6), canonicalUser(EVENT_ID, 7)],
      [optimisticUser()]
    )

    expect(entries.map(entry => entry.seq)).toEqual([6, 7, undefined])
  })

  it('does not equate idless hosted text with an accepted user event', async () => {
    expect((await merge(userRoom([canonicalUser()]), [optimisticUser({ id: undefined })])).log).toHaveLength(2)
    const chat = await import('./group-chat')
    const classic = optimisticUser()
    expect(chat.mergeGroupChatSyncEntries([classic], [{ ...classic, id: undefined }])).toHaveLength(1)
  })

  it('keeps a compact canonical marker when two projections meet without a local receipt', async () => {
    const chat = await import('./group-chat')
    const old = chat.groupChatSyncSnapshot({ Board: userRoom([canonicalUser(CLIENT_ID, 4)]) })
    const modern = chat.groupChatSyncSnapshot({ Board: userRoom([canonicalUser()]) })
    const merged = JSON.parse(JSON.stringify(chat.mergeGroupChatSyncSnapshots(old, modern)))

    expect(merged.rooms['id:room-1'].log.map((entry: GroupMessage) => entry.eventId)).toEqual([CLIENT_ID, EVENT_ID])
  })

  it('keeps one rich pending user entry when its same-ID compact mirror arrives before acceptance', async () => {
    const message = optimisticUser({ roomId: 'room-1', clientEventId: CLIENT_ID, text: 'pending '.repeat(200) })
    const restored = await merge(userRoom([message]), [message])

    expect(restored.log).toEqual([message])
  })

  it('cannot bind an explicit foreign-room mirror to an older canonical record lacking room metadata', async () => {
    const canonical = canonicalUser(EVENT_ID, 6, { roomId: undefined })
    const foreign = { ...canonical, roomId: 'foreign-room', seq: undefined }
    const restored = await merge(userRoom([canonical]), [foreign])

    expect(restored.log).toHaveLength(2)
    expect(restored.log.find(entry => entry.seq === 6)?.roomId).toBe('room-1')
    expect(restored.log.find(entry => entry.roomId === 'foreign-room')?.seq).toBeUndefined()
  })

  it('retains a canonical marker for older seq/id-only raw UUID records when compacting', async () => {
    const chat = await import('./group-chat')
    const old = canonicalUser(CLIENT_ID, 4, { eventId: undefined, roomId: undefined })
    const cold = JSON.parse(JSON.stringify(chat.groupChatSyncSnapshot({ Board: userRoom([old]) })))
    const restored = chat.mergeRemoteGroupChatSnapshotIntoRooms(cold, { Board: userRoom([canonicalUser()]) }).Board

    expect(restored.log).toHaveLength(2)
    expect(restored.log.find(entry => entry.id === CLIENT_ID)?.eventId).toBe(CLIENT_ID)
  })
})
