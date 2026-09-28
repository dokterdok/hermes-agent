import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type * as GroupChatParts from './group-chat-parts'
import { createGroupGateway, scriptedStorage } from './group-test-utils'
import { translateBots } from './i18n-test-helper'
import type { ShippedGroupAdoption } from './types'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')
  const base = await pluginSdkMock(host)
  const { en } = await import('@/i18n/en')
  const { Textarea } = await import('@/components/ui/textarea')
  const { useStore } = await import('@nanostores/react')

  const Button = (props: ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} />

  return { ...base, host, useValue: useStore, Button, RowButton: Button,
    cn: (...values: unknown[]) => values.filter(Boolean).join(' '),
    Codicon: () => null, CopyButton: () => null, ConfirmDialog: () => null,
    Dialog: () => null, DialogContent: () => null, DialogDescription: () => null,
    DialogFooter: () => null, DialogHeader: () => null, DialogTitle: () => null,
    Input: () => null, ToggleRow: () => null,
    Textarea,
    Tip: ({ children }: { children: ReactNode }) => children, relativeTime: () => 'now',
    MessageTextContent: ({ text }: { text: string }) => <span>{text}</span>,
    useI18n: () => ({ locale: 'en', t: en }), usePluginI18n: () => translateBots }
})
vi.mock('./avatar', () => ({ avatarColor: () => '#888', botAppearance: () => ({}), BotFace: () => null }))
vi.mock('./group-chat-parts', async importOriginal => ({
  ...await importOriginal<typeof GroupChatParts>(), GroupImageControls: () => null
}))

Object.assign(host, createGroupGateway().host)

const [ { GroupChatWorkspace }, chat, adoption, { setPluginCtx }, { hostedRoomObservations } ] = await Promise.all([
  import('./group-chat-view'), import('./group-chat'), import('./shipped-group-adoption'), import('./shared'), import('./hosted-room-runtime')
])

const members = [{ name: 'research', connectionId: 'owner' }, { name: 'builder', connectionId: 'owner' }]
let gateway: ReturnType<typeof createGroupGateway>
let ctx: ReturnType<typeof scriptedStorage>
const state = { connectionId: atom('owner'), profile: atom('default'), gateway: atom('open') }

function capabilities(driver: boolean) {
  // Foundation producer shape: canonical driver readiness does not imply an importer.
  return { driver, persistent_process: true, protocol_version: 2, authority_gateway_id: 'owner-install',
    methods: ['groups.capabilities', 'groups.create', 'groups.state', 'groups.send'],
    features: ['room_identity', 'monotonic_log', 'coordinator_fencing'] }
}

function answer(driver: boolean) {
  const request = gateway.host.request as (method: string, params: Record<string, unknown>) => Promise<unknown>
  host.request = async (method: string, params: Record<string, unknown>) =>
    method === 'groups.capabilities' ? capabilities(driver) : request(method, params)
  host.requestProfile = async (_route: unknown, method: string, params: Record<string, unknown>) =>
    (host.request as typeof request)(method, params)
  host.acquireProfileRoute = async (route: unknown) => ({ generation: 1, route,
    assertCurrent() {}, release() {}, request: host.request })
}

beforeEach(async () => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
  gateway = createGroupGateway()
  state.gateway.set('open')
  Object.assign(host, gateway.host, { state, activeConnectionId: () => 'owner',
    profileRoutes: async () => [{ connectionId: 'owner', mode: 'local', profile: 'default', targetProfile: 'default' }] })
  ctx = scriptedStorage(gateway.storage)
  setPluginCtx(ctx)
  chat.$groupChats.set({ Classic: { roomId: 'classic-ui-room', log: [], members, watermarks: {} } })
  await chat.activateClassicGroupAuthorities()
  hostedRoomObservations.publish(hostedRoomObservations.capture('owner'), new Set(), true)
  chat.stopGroupChatServerSync()
})
afterEach(() => {
  cleanup()
  adoption.stopShippedGroupAdoption()
  chat.stopGroupChatServerSync()
  vi.unstubAllGlobals()
  localStorage.clear()
})

it.each([true, false])('retains a usable classic composer without an importer (canonical driver=%s)', async driver => {
  answer(driver)
  await adoption.adoptShippedGroupChats(ctx.storage)
  expect(chat.$groupChats.get().Classic.shippedAdoption).toBeUndefined()
  expect(chat.$groupChats.get().Classic.shippedPreflight?.issue?.kind).toBe('update-required')
  render(<GroupChatWorkspace group="Classic" members={members} />)
  const composer = await screen.findByRole('textbox', { name: 'Message Classic' })
  fireEvent.change(composer, { target: { value: 'Send from the retained classic UI' } })
  fireEvent.click(screen.getByRole('button', { name: 'New Thread' }))
  await waitFor(() => expect(gateway.calls.length).toBeGreaterThan(0))
  await waitFor(() => expect(chat.$groupChats.get().Classic.running).toBe(false))
  expect(chat.$groupChats.get().Classic.log.some(entry => entry.text === 'Send from the retained classic UI')).toBe(true)
})

it.each(['waiting', 'prepared', 'uncertain', 'adopted', 'offline', 'inventory', 'rpc'] as const)('does not bypass an actual %s execution hold', async hold => {
  answer(true)
  await adoption.adoptShippedGroupChats(ctx.storage)

  if (hold === 'rpc') { host.requestProfile = async () => { throw new Error('Route offline') } }
  else if (hold === 'offline') { state.gateway.set('closed') }
  else if (hold === 'inventory') {
    chat.updateGroupChat('Classic', room => ({ ...room, members: members.map(member => ({ ...member, remoteSource: true })) }))
    hostedRoomObservations.invalidate('owner')
  } else {
    const checkpoint: ShippedGroupAdoption = { ...chat.$groupChats.get().Classic.shippedPreflight!,
      state: hold === 'uncertain' ? 'prepared' : hold,
      ...(hold === 'uncertain' ? { issue: { kind: 'offline', message: 'Import outcome unknown' } } : {}) }

    chat.updateGroupChat('Classic', room => ({ ...room, shippedAdoption: checkpoint }))
  }

  await act(async () => { render(<GroupChatWorkspace group="Classic" members={members} />) })
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(gateway.calls).toHaveLength(0)
})
