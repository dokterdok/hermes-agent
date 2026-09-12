import type * as HermesSdk from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { translateBots } from './i18n-test-helper'
const request = vi.hoisted(() => vi.fn())
const notify = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const original = await importOriginal<typeof HermesSdk>()
  return { ...original, usePluginI18n: () => translateBots, host: { ...original.host,
    state: { ...original.host.state, connectionId: atom('local'), profile: atom('default') },
    connections: vi.fn(async () => []), notify, notifyError: vi.fn(),
    request: vi.fn(async () => ({})), requestProfile: request } }
})

import { CreateGroupChatDialog } from './create-dialog'
import { readCanonicalGroupCreate } from './canonical-group-create'
import { $groupChats } from './group-chat'

const originalDesktop = window.hermesDesktop
const route = { connectionId: 'local', profile: 'default' }
const roster = [{ name: 'writer', connectionId: 'local' }, { name: 'reviewer', connectionId: 'local' }]
let entries: Record<string, unknown>
beforeEach(() => {
  entries = {}
  window.hermesDesktop = { preparedSubmissions: {
    read: async () => JSON.stringify(entries), update: async () => {},
    compareAndSet: async (key: string, expected: string | null, entry: string | null) => {
      if (JSON.stringify(entries[key] ?? null) !== (expected ?? 'null')) {return false}
      if (entry === null) {delete entries[key]} else {entries[key] = JSON.parse(entry)}
      return true
    }
  } } as unknown as typeof window.hermesDesktop
  Element.prototype.scrollIntoView = () => undefined
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = () => undefined
  Element.prototype.setPointerCapture = () => undefined
})
afterEach(() => { cleanup(); window.hermesDesktop = originalDesktop; request.mockReset(); notify.mockReset(); localStorage.clear() })

async function chooseGroup() {
  for (const checkbox of screen.getAllByRole('checkbox')) {fireEvent.click(checkbox)}
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Saved team' } })
  const create = screen.getByRole('button', { name: /^Create Group/ })
  await waitFor(() => expect((create as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(create)
}

it('offers the saved setup after failure and reopening, without new selections or an automatic retry', async () => {
  let first = true
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}
    if (method === 'groups.create') {
      if (first) {first = false; throw new Error('Unconfirmed setup')}
      return { room: { ...params, authority_gateway_id: 'install:home' } }
    }
    return {}
  })
  const closed = vi.fn(), created = vi.fn()
  const view = render(<CreateGroupChatDialog open roster={roster} onClose={closed} onCreated={created} />)
  await chooseGroup()
  await screen.findByRole('button', { name: 'Continue setup' })
  const entry = await readCanonicalGroupCreate(route)
  expect(entry?.params.name).toBe('Saved team')
  expect(created).not.toHaveBeenCalled()
  view.unmount()
  render(<CreateGroupChatDialog open roster={[]} onClose={closed} onCreated={created} />)
  const resume = await screen.findByRole('button', { name: 'Continue setup' })
  expect(screen.getByText('Saved team')).toBeTruthy()
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
  fireEvent.click(resume)
  await waitFor(() => expect(created).toHaveBeenCalledTimes(1))
  expect(closed).toHaveBeenCalledTimes(1)
  expect(request.mock.calls.filter(call => call[1] === 'groups.create').map(call => call[2].room_id)).toEqual([
    entry?.binding.roomId, entry?.binding.roomId
  ])
  expect(await readCanonicalGroupCreate(route)).toBeUndefined()
})

it.each([{ driver: false, features: ['canonical_session_owner'] }, {}])('never starts a legacy room from canonical unavailability or an invalid capability reply: %j', async capabilities => {
  const before = $groupChats.get()
  request.mockImplementation(async (_route, method) => method === 'groups.capabilities' ? capabilities : {})
  const created = vi.fn()
  render(<CreateGroupChatDialog open roster={roster} onClose={() => {}} onCreated={created} />)
  await chooseGroup()
  await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error' })))
  expect(request.mock.calls.some(call => call[1] === 'groups.create')).toBe(false)
  expect($groupChats.get()).toEqual(before)
  expect(created).not.toHaveBeenCalled()
})
