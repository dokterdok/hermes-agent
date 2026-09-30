import { afterEach, describe, expect, it, vi } from 'vitest'

import { boundedDesktopCommandSettled, desktopCommandResult, settleDesktopCommand } from './group-command-receipts'
import { classicAuthorityHash } from './group-desktop-authority'
import type { GroupChat } from './types'

const makeRoom = (): GroupChat => ({
  roomId: 'room-1',
  log: [],
  watermarks: {},
  members: [{ name: 'builder' }],
  desktopAuthorityToken: 'authority:private',
  desktopAuthorityHash: classicAuthorityHash('authority:private')
})

afterEach(() => vi.useRealTimers())

describe('bounded Desktop command completion receipts', () => {
  it('pins unacknowledged results through pressure, tied timestamps and hydration limits', () => {
    vi.useFakeTimers()
    const room = makeRoom()

    for (let index = 0; index < 160; index++) {
      room.desktopCommandSettled = settleDesktopCommand('Workshop', room, `send:${index}`, 'send', {
        room_name: 'Workshop',
        thread_id: `thread:${index}`
      })
    }

    expect(Object.keys(room.desktopCommandSettled!)).toHaveLength(160)
    expect(desktopCommandResult('Workshop', room, 'send:0', 'send')).toEqual({
      room_name: 'Workshop', thread_id: 'thread:0'
    })
    expect(desktopCommandResult('Workshop', room, 'send:159', 'send')).toEqual({
      room_name: 'Workshop', thread_id: 'thread:159'
    })
    expect(Object.keys(boundedDesktopCommandSettled(room.desktopCommandSettled))).toHaveLength(160)
  })

  it('does not infer safe retirement from unqualified ACK flags; legacy and corrupt markers survive pressure', () => {
    const room = makeRoom()
    room.desktopCommandSettled = settleDesktopCommand('Workshop', room, 'unacknowledged', 'send', {
      room_name: 'Workshop', thread_id: 'original'
    })
    const saved = room.desktopCommandSettled.unacknowledged

    const completed = Object.fromEntries(Array.from({ length: 160 }, (_, index) => [
      `completed:${index}`, { ...(saved as object), at: index + 1, providerCompleted: true }
    ]))

    const retained = boundedDesktopCommandSettled({ ...completed, ...room.desktopCommandSettled, legacy: 0, corrupt: null })
    expect(Object.keys(retained)).toHaveLength(163)
    expect(retained['completed:0']).toBeDefined()
    expect(retained['completed:159']).not.toHaveProperty('providerCompleted')
    expect(retained.unacknowledged).toEqual(saved)
    expect(retained.legacy).toBe(0)
    expect(retained.corrupt).toBe(0)
    room.desktopCommandSettled = boundedDesktopCommandSettled(JSON.parse(JSON.stringify(retained)))
    expect(() => desktopCommandResult('Workshop', room, 'legacy', 'send')).toThrow('already settled')
    expect(() => desktopCommandResult('Workshop', room, 'corrupt', 'send')).toThrow('already settled')
  })

  it('preserves the exact first result through JSON restart and stable-id rename', () => {
    const room = makeRoom()
    room.desktopCommandSettled = settleDesktopCommand('Workshop', room, 'send:1', 'send', {
      room_name: 'Workshop',
      thread_id: 'original'
    })
    const cold = JSON.parse(JSON.stringify(room)) as GroupChat
    cold.desktopCommandSettled = boundedDesktopCommandSettled(cold.desktopCommandSettled)
    cold.desktopCommandSettled = settleDesktopCommand('Renamed', cold, 'send:1', 'send', {
      room_name: 'Renamed',
      stopped: true
    })
    expect(desktopCommandResult('Renamed', cold, 'send:1', 'send')).toEqual({
      room_name: 'Workshop',
      thread_id: 'original'
    })
  })

  it.each([
    { room_name: 'Workshop', stopped: true },
    { room_name: 'Workshop', stopped: true, stale: true as const },
    { room_name: 'Workshop', stopped: false, stale: true as const }
  ])('retains exact Stop semantics: %j', result => {
    const room = makeRoom()
    room.desktopCommandSettled = settleDesktopCommand('Workshop', room, 'stop:1', 'stop', result)
    expect(desktopCommandResult('Workshop', JSON.parse(JSON.stringify(room)), 'stop:1', 'stop')).toEqual(result)
    expect(() => desktopCommandResult('Workshop', room, 'stop:1', 'send')).toThrow('already settled')
  })

  it.each([123, 0, { at: 1, result: null }, null])(
    'keeps unrecoverable legacy/corrupt settlement fail-closed: %j',
    old => {
      const room = makeRoom()
      room.desktopCommandSettled = boundedDesktopCommandSettled({ old })
      expect(Object.hasOwn(room.desktopCommandSettled, 'old')).toBe(true)
      expect(() => desktopCommandResult('Workshop', room, 'old', 'send')).toThrow('already settled')
      expect(
        settleDesktopCommand('Workshop', room, 'old', 'send', { room_name: 'Workshop', thread_id: 'new' })
      ).toEqual(room.desktopCommandSettled)
    }
  )

  it('rejects receipts copied into a different incarnation or canonical identity', () => {
    const room = makeRoom()
    room.desktopCommandSettled = settleDesktopCommand('Workshop', room, 'send:1', 'send', {
      room_name: 'Workshop',
      thread_id: 'original'
    })
    expect(() => desktopCommandResult('Workshop', { ...room, roomId: 'new' }, 'send:1', 'send')).toThrow(
      'already settled'
    )
    expect(() =>
      desktopCommandResult('Workshop', { ...room, desktopAuthorityHash: classicAuthorityHash('new') }, 'send:1', 'send')
    ).toThrow('already settled')
  })

  it('does not truncate command IDs into collisions or retain extra private fields', () => {
    const room = makeRoom()
    room.desktopCommandSettled = settleDesktopCommand('Workshop', room, 'send:1', 'send', {
      room_name: 'Workshop',
      thread_id: 'original'
    })
    const saved = room.desktopCommandSettled['send:1'] as object
    const bounded = boundedDesktopCommandSettled({ 'send:1': { ...saved, token: 'private' }, ['x'.repeat(161)]: 1 })
    expect(Object.keys(bounded)).toEqual(['send:1'])
    expect(JSON.stringify(bounded)).not.toContain('private')
  })
})
