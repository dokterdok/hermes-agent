import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopRoomCommand } from './desktop-room-command-client'
import { loadRetentionEngine, members, route, settle } from './desktop-room-mailbox-retention-fixtures'
import type { RetentionEngine } from './desktop-room-mailbox-retention-fixtures'
import { classicAuthorityHash } from './group-desktop-authority'
import { createGroupGateway, scriptedStorage } from './group-test-utils'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))
const authorityToken = 'authority:retention-fixture'

vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')

  return pluginSdkMock(host)
})

Object.assign(host, createGroupGateway().host)
await Promise.all([import('./group-chat'), import('./group-rounds'), import('./desktop-room-command-runtime')])

beforeEach(() => vi.useFakeTimers())
afterEach(async () => {
  const runtime = await import('./desktop-room-command-runtime')
  runtime.stopDesktopRoomCommandRuntime()
  vi.clearAllTimers()
  vi.useRealTimers()
})

async function coldRestore(loaded: RetentionEngine) {
  await loaded.chat.persistGroupChatRoomsRequired()
  const snapshot = JSON.parse(JSON.stringify(loaded.gateway.storage.get('group-chats')))
  vi.resetModules()

  const [chat, client, rounds, runtime, data, shared] = await Promise.all([
    import('./group-chat'), import('./desktop-room-command-client'), import('./group-rounds'),
    import('./desktop-room-command-runtime'), import('./data'), import('./shared')
  ])

  shared.setPluginCtx(scriptedStorage(loaded.gateway.storage))
  chat.$groupChats.set(chat.hydrateGroupChatRooms(snapshot))
  data.$lastRoster.set(members)

  return { ...loaded, chat, client, rounds, runtime }
}

function command(id: string, action = 'send', payload: Record<string, unknown> = {
  message: 'Do this work once', recipients: members
}): DesktopRoomCommand {
  return { command_id: id, room_id: 'room-1', action, payload, attempts: 1, lease_token: `lease:${id}:first` }
}

/** Script only delivery and request loss. Public33f has no SQLite mailbox or
 * groups.desktop RPC handlers: these tests prove the actual public consumer,
 * not provider reclaim eligibility, expiry, purge or committed-response loss. */
function deliver(loaded: RetentionEngine, claimedCommand: DesktopRoomCommand, consumerId: string, loseCompletion = false) {
  let claimed = false
  const completions: Array<Record<string, unknown>> = []

  const promise = loaded.client.runDesktopRoomCommandCycle({
    consumerId, rooms: loaded.chat.$groupChats.get(), routes: [route], actions: [String(claimedCommand.action)],
    execute: loaded.runtime.executeDesktopRoomCommand,
    request: async (_route, method, params) => {
      if (method === 'groups.desktop.claim') {
        expect(params.room_authorities).toEqual([{ room_id: 'room-1', authority_token: authorityToken }])

        if (claimed) {return { commands: [] }}
        claimed = true

        return { commands: [structuredClone(claimedCommand)] }
      }

      if (method === 'groups.desktop.complete') {
        completions.push(structuredClone(params))

        if (loseCompletion) {throw new Error('completion request lost before provider dispatch')}

        return {}
      }

      throw new Error('Unexpected mailbox request: ' + method)
    }
  })

  return { promise, completions }
}

// Targeted mailbox Stop already owns receipt creation on the public runtime.
// Direct UI Stop's missing receipt creation is a separate group-rounds prerequisite.
describe('public classic consumer receipt retention (scripted transport, no provider claim)', () => {
  it.each(['mailbox stopped', 'settled then history trimmed'] as const)(
    'never reexecutes %s work after lost completion, 128 later receipts and cold delivery', async mode => {
      let release!: (text: string) => void

      const loaded = await loadRetentionEngine(host, {
        turn: () => new Promise<string>(resolve => { release = resolve })
      })

      loaded.chat.$groupChats.set({ Workshop: {
        log: [], members, roomId: 'room-1', sessions: {}, watermarks: {}, holdDetection: false,
        desktopAuthorityToken: authorityToken,
        desktopAuthorityHash: classicAuthorityHash(authorityToken)
      } })
      vi.setSystemTime(1_000_000)
      const first = command('mailbox:retention')
      const initial = deliver(loaded, first, 'desktop:one', true)
      await vi.advanceTimersByTimeAsync(0)
      expect(loaded.gateway.calls).toHaveLength(1)

      if (mode === 'mailbox stopped') {
        const stop = deliver(loaded, command('mailbox:stop-original', 'stop', { target_command_id: first.command_id }), 'desktop:one')
        expect(await settle(stop.promise)).toMatchObject([{ success: true }])
      }

      release('(pass)')
      await settle(initial.promise)
      expect(initial.completions).toHaveLength(2)
      expect(initial.completions.every(completion => completion.success === true)).toBe(true)
      const original = structuredClone(initial.completions[0].result)
      expect(original).toMatchObject(mode === 'mailbox stopped'
        ? { room_name: 'Workshop', stopped: true }
        : { room_name: 'Workshop', thread_id: expect.any(String) })
      loaded.chat.updateGroupChat('Workshop', room => ({ ...room,
        desktopCommandSettled: { ...room.desktopCommandSettled, 'legacy:unknown': 0 }
      }))

      for (let index = 0; index < 128; index++) {
        vi.setSystemTime((1_101 + index) * 1000)
        const pressure = command(`mailbox:pressure:${index}`, 'stop', { target_message_id: 'already-gone-message' })
        expect(await settle(deliver(loaded, pressure, 'desktop:one').promise)).toMatchObject([{ success: true }])
      }

      if (mode === 'settled then history trimmed') {
        for (let index = 0; index <= loaded.chat.GROUP_CHAT_LOG_RETAIN; index++) {
          loaded.chat.appendGroupChatEntry('Workshop', { kind: 'user', name: 'You' }, 'Later history', 'later-thread', undefined, {
            entryId: `later:${index}`
          })
        }

        expect(loaded.chat.$groupChats.get().Workshop.log.some(entry => entry.id === first.command_id)).toBe(false)
      }

      const cold = await coldRestore(loaded)
      const before = structuredClone(cold.chat.$groupChats.get().Workshop)
      vi.setSystemTime(1_319_000)
      const reclaimed = deliver(cold, { ...first, attempts: 2, lease_token: 'lease:retention:second' }, 'desktop:two')
      await vi.advanceTimersByTimeAsync(500)

      // Release an erroneous second submit so the causal public-base RED is bounded.
      if (loaded.gateway.calls.length > 1) {release('(pass)')}
      await settle(reclaimed.promise)
      expect(loaded.gateway.calls).toHaveLength(1)
      expect(reclaimed.completions[0]?.result).toEqual(original)
      expect(before.desktopCommandSettled?.[String(first.command_id)]).toMatchObject({ result: original })
      expect(before.desktopCommandSettled?.['legacy:unknown']).toBe(0)
      expect(Object.keys(before.desktopCommandSettled || {})).toHaveLength(mode === 'mailbox stopped' ? 131 : 130)
      expect(cold.chat.$groupChats.get().Workshop.log).toEqual(before.log)
      expect(cold.chat.$groupChats.get().Workshop.watermarks).toEqual(before.watermarks)
      const later = deliver(cold, command('mailbox:later'), 'desktop:two')
      await vi.advanceTimersByTimeAsync(0)
      expect(loaded.gateway.calls).toHaveLength(2)
      release('(pass)')
      expect(await settle(later.promise)).toMatchObject([{ success: true }])
      expect(cold.chat.$groupChats.get().Workshop.desktopCommandSettled?.[String(first.command_id)])
        .toEqual(before.desktopCommandSettled?.[String(first.command_id)])
      expect(cold.chat.$groupChats.get().Workshop.desktopCommandSettled?.['legacy:unknown']).toBe(0)
    }
  )
})
