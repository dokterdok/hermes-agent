import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const transport = vi.hoisted(() => ({ request: vi.fn() }))
const controls = vi.hoisted(() => ({ connection: (_value: string) => {}, profile: (_value: string) => {}, gateway: (_value: string) => {} }))
vi.mock('@hermes/plugin-sdk', () => {
  const state = { connectionId: atom('owner-a'), profile: atom('default'), gateway: atom('open') }
  controls.connection = value => state.connectionId.set(value)
  controls.profile = value => state.profile.set(value)
  controls.gateway = value => state.gateway.set(value)
  return { atom, useValue: useStore, Button: (props: ComponentProps<'button'>) => <button {...props} />,
    host: { requestProfile: transport.request, state } }
})
vi.mock('./canonical-group-labels', () => ({ useCanonicalGroupLabels: () => ({ refreshGroups: 'Refresh groups' }) }))
import { $canonicalGroupBindings, CanonicalGroupList } from './canonical-group-registry'

beforeEach(() => { controls.connection('owner-a'); controls.profile('default'); controls.gateway('open') })
afterEach(() => { cleanup(); transport.request.mockReset(); $canonicalGroupBindings.set({}) })

it('retains last-known groups while disconnected and refreshes automatically on reconnect', async () => {
  let title = 'Existing group'
  transport.request.mockImplementation(async (_route, method) => method === 'groups.capabilities'
    ? { driver: true } : { rooms: [{ room_id: 'same-room', name: title, members: [] }], next_offset: null })
  render(<CanonicalGroupList onOpen={() => {}} />)
  await screen.findByRole('button', { name: 'Existing group' })
  act(() => { controls.gateway('closed') })
  const count = transport.request.mock.calls.length
  expect(screen.getByRole('button', { name: 'Existing group' })).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Refresh groups' }) as HTMLButtonElement).disabled).toBe(true)
  title = 'Renamed elsewhere'
  act(() => { controls.gateway('open') })
  await screen.findByRole('button', { name: title })
  expect(transport.request.mock.calls.length).toBeGreaterThan(count)
  expect(transport.request.mock.calls.every(call => call[0].connectionId === 'owner-a')).toBe(true)
})

it('never publishes a late inventory from the previous connection', async () => {
  let release!: (value: unknown) => void
  transport.request.mockImplementation(async (route, method) => {
    if (method === 'groups.capabilities') {return { driver: true }}
    if (route.connectionId === 'owner-a') {return new Promise(resolve => { release = resolve })}
    return { rooms: [{ room_id: 'room-b', name: 'New owner group', members: [] }], next_offset: null }
  })
  render(<CanonicalGroupList onOpen={() => {}} />)
  await waitFor(() => expect(release).toBeTypeOf('function'))
  act(() => { controls.connection('owner-b') })
  await screen.findByRole('button', { name: 'New owner group' })
  await act(async () => { release({ rooms: [{ room_id: 'room-a', name: 'Old private group', members: [] }], next_offset: null }) })
  expect(screen.queryByText('Old private group')).toBeNull()
  expect(Object.values($canonicalGroupBindings.get()).map(value => value.connectionId)).toEqual(['owner-b'])
})
