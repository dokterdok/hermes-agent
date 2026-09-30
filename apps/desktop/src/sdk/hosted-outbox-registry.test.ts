import type { PluginContext } from '@hermes/plugin-sdk'
import { afterEach, expect, it, vi } from 'vitest'

import { host } from '@/sdk'
import {
  closeSecondaryGateways,
  configureGatewayRegistry,
  disposeSecondariesForConnection,
  setPrimaryGateway,
  setPrimaryGatewayConnection
} from '@/store/gateway'

import type { HostedRoomCommand, HostedRoomOutbox } from '../plugins/hermes-bots/hosted-room-client'
import type * as Runtime from '../plugins/hermes-bots/hosted-room-runtime'

// Production consumer -> actual SDK -> actual registry/route lease. Native
// endpoint discovery and the decoded socket responder are the external seams.
const wire = vi.hoisted(() => ({
  installation: 'install:home',
  capabilities: 0,
  pause: null as null | { index: number; reached: () => void; pending: Promise<void> },
  calls: [] as Array<{ installation: string; method: string; params: Record<string, unknown> }>
}))

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<Record<string, unknown>>()),
  HermesGateway: class {
    connectionState = 'closed'
    installation = ''
    onEvent = () => () => undefined
    onState = () => () => undefined
    close = () => (this.connectionState = 'closed')
    connect = async () => {
      this.installation = wire.installation
      this.connectionState = 'open'
    }

    request = async <T>(method: string, params: Record<string, unknown> = {}): Promise<T> => {
      const installation = this.installation
      wire.calls.push({ installation, method, params: structuredClone(params) })

      if (method === 'groups.capabilities') {
        const index = ++wire.capabilities

        if (wire.pause?.index === index) {
          const gate = wire.pause
          wire.pause = null
          gate.reached()
          await gate.pending
        }

        return { driver: true, persistent_process: true, authority_gateway_id: installation } as T
      }

      if (method === 'groups.send') {
        return { accepted: true, client_event_id: params.event_id, event: { room_id: params.room_id, kind: 'message.user' } } as T
      }

      throw new Error(`Unexpected wire method: ${method}`)
    }
  }
}))

function barrier() {
  let entered!: () => void
  let release!: () => void
  const reached = new Promise<void>(resolve => (entered = resolve))
  const pending = new Promise<void>(resolve => (release = resolve))

  return { reached, pending, entered, release }
}

const KEY = 'hosted-room-outbox-v1'
const route = { connectionId: 'background', profile: 'default', targetProfile: 'default', mode: 'remote' as const }

const intent: HostedRoomCommand = {
  commandId: 'private', roomId: 'room', connectionId: 'background', authorityId: 'install:home',
  kind: 'send', status: 'pending', attempts: 0, failureCode: null, possibleAdmission: false,
  payload: { text: 'private text for A', thread_id: 'thread', attachments: [{ attachment_id: 'private-reference' }] }
}

let runtime: typeof Runtime | null = null
const originalDesktop = window.hermesDesktop

async function setup(boundary: 'discovery' | 'claim' | 'revalidation' | 'none') {
  vi.useFakeTimers()
  wire.installation = 'install:home'
  wire.capabilities = 0
  wire.calls = []
  wire.pause = null
  configureGatewayRegistry({ onEvent: vi.fn() })
  closeSecondaryGateways()
  setPrimaryGateway({ connectionState: 'open', request: vi.fn() } as never, 'default')
  setPrimaryGatewayConnection({ connectionId: 'foreground' })
  window.hermesDesktop = {
    getConnectionFor: vi.fn(async () => ({ port: 5151, profile: 'default', sharedRemote: false })),
    getGatewayWsUrlFor: vi.fn(async () => ({ ok: true, wsUrl: 'ws://inert.invalid' }))
  } as unknown as typeof window.hermesDesktop
  let routes: typeof route[] = []
  vi.spyOn(host, 'profileRoutes').mockImplementation(async () => routes)
  const values = new Map<string, unknown>()
  let claimGate: ReturnType<typeof barrier> | null = null

  const storage = {
    get: <T>(key: string, fallback?: T) => structuredClone(values.get(key) ?? fallback ?? null) as T,
    set: async (key: string, value: unknown) => {
      if (key === KEY && claimGate && (value as HostedRoomOutbox).commands.some(command => command.status === 'in-flight')) {
        const held = claimGate
        claimGate = null
        held.entered()
        await held.pending
      }

      values.set(key, structuredClone(value))
    },
    remove: (key: string) => values.delete(key)
  } as unknown as PluginContext['storage']

  runtime = await import('../plugins/hermes-bots/hosted-room-runtime')
  await runtime.startHostedRoomRuntime(storage)
  routes = [route]
  const outbox = await import('../plugins/hermes-bots/hosted-room-outbox')
  await outbox.mutateHostedRoomOutbox(storage, { type: 'enqueue', command: intent })
  const gate = barrier()

  if (boundary === 'claim') {
    claimGate = gate
  } else if (boundary !== 'none') {
    wire.pause = { index: boundary === 'discovery' ? 1 : 2, reached: gate.entered, pending: gate.pending }
  }

  return { outbox, storage, values, gate, runtime }
}

afterEach(() => {
  runtime?.stopHostedRoomRuntime()
  closeSecondaryGateways()
  setPrimaryGateway(null)
  vi.restoreAllMocks()
  vi.clearAllTimers()
  vi.useRealTimers()
  window.hermesDesktop = originalDesktop
})

it.each(['discovery', 'claim', 'revalidation'] as const)(
  'does not disclose queued text or attachment references through actual SDK after edit/remove-readd at %s',
  async boundary => {
    const loaded = await setup(boundary)
    const dispatch = loaded.runtime.dispatchHostedRoomOutbox()
    await loaded.gate.reached
    disposeSecondariesForConnection('background', { redial: true })
    disposeSecondariesForConnection('background')
    wire.installation = 'install:replacement'
    loaded.gate.release()
    await dispatch
    expect(wire.calls.filter(call => call.method === 'groups.send')).toEqual([])
    expect((await loaded.outbox.readHostedRoomOutbox(loaded.storage)).commands[0]).toMatchObject({
      commandId: 'private', authorityId: 'install:home', payload: intent.payload
    })
  }
)

it('uses the same actual leased socket for revalidation and private dispatch with an unchanged owner', async () => {
  const loaded = await setup('none')
  await loaded.runtime.dispatchHostedRoomOutbox()
  expect(wire.calls.filter(call => call.method === 'groups.send')).toEqual([
    { installation: 'install:home', method: 'groups.send', params: { room_id: 'room', event_id: 'private', payload: intent.payload } }
  ])
  expect((await loaded.outbox.readHostedRoomOutbox(loaded.storage)).commands).toEqual([])
})

it('invalidates leased revalidation on same-ID/same-installation ABA and permits fresh correctly qualified work', async () => {
  const loaded = await setup('revalidation')
  const dispatch = loaded.runtime.dispatchHostedRoomOutbox()
  await loaded.gate.reached
  disposeSecondariesForConnection('background')
  loaded.gate.release()
  await dispatch
  expect(wire.calls.filter(call => call.method === 'groups.send')).toEqual([])
  await loaded.runtime.dispatchHostedRoomOutbox()
  expect(wire.calls.filter(call => call.method === 'groups.send')).toHaveLength(1)
  expect((await loaded.outbox.readHostedRoomOutbox(loaded.storage)).commands).toEqual([])
})
