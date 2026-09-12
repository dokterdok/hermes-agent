import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { PreparedCanonicalGroupCreate } from './canonical-group-create'
import { CanonicalGroupCreateRecovery } from './canonical-group-create-recovery'
import { translateBots } from './i18n-test-helper'

const state = vi.hoisted(() => ({ epoch: 1, resume: vi.fn(), register: vi.fn() }))
vi.mock('./canonical-group-create', () => ({ resumeCanonicalGroupCreate: state.resume }))
vi.mock('./canonical-group-registry', () => ({ registerCanonicalGroup: state.register }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return { ...sdk, gatewayActivationEpoch: () => state.epoch,
    usePluginI18n: () => translateBots, useI18n: () => ({ locale: 'en', t: en }),
    host: { ...sdk.host, state: { ...sdk.host.state,
      connectionId: sdk.atom('home'), profile: sdk.atom('default'), gateway: sdk.atom('open') } } }
})

const entry: PreparedCanonicalGroupCreate = {
  version: 1, binding: { connectionId: 'home', profile: 'default', roomId: 'original' },
  authorityId: 'install:home', params: { room_id: 'original', name: 'Saved team',
    members: ['writer', 'reviewer'].map(profile => ({ member_id: profile, profile, handle: profile })) }
}
const result = { binding: entry.binding, room: { ...entry.params, authority_gateway_id: entry.authorityId } }
const connection = host.state.connectionId as WritableAtom<string>
const profile = host.state.profile as WritableAtom<string>
const gateway = host.state.gateway as WritableAtom<string>

beforeEach(() => {
  state.epoch = 1
  state.resume.mockReset()
  state.register.mockReset().mockReturnValue('canonical:original')
  connection.set('home'); profile.set('default'); gateway.set('open')
})
afterEach(cleanup)

function show() {
  const onClose = vi.fn(), onCreated = vi.fn()
  render(<CanonicalGroupCreateRecovery entry={entry} onClose={onClose} onCreated={onCreated} open />)
  fireEvent.click(screen.getByRole('button', { name: 'Continue setup' }))
  return { onClose, onCreated }
}

it('opens the original saved group after a current-source acknowledgement', async () => {
  state.resume.mockResolvedValue(result)
  const callbacks = show()
  await act(async () => {})
  expect(state.resume).toHaveBeenCalledExactlyOnceWith(entry.binding, entry.binding.roomId)
  expect(state.register).toHaveBeenCalledExactlyOnceWith(result.binding, result.room)
  expect(callbacks.onCreated).toHaveBeenCalledWith('canonical:original')
})

it.each(['return-to-source', 'replace-same-route'])('does not adopt an old completion after %s', async change => {
  let finish!: (value: typeof result) => void
  state.resume.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const callbacks = show()
  await act(async () => {
    if (change === 'return-to-source') {connection.set('other'); state.epoch++; connection.set('home')}
    state.epoch++
    finish(result)
  })
  expect(state.resume).toHaveBeenCalledExactlyOnceWith(entry.binding, entry.binding.roomId)
  expect(state.register).not.toHaveBeenCalled()
  expect(callbacks.onCreated).not.toHaveBeenCalled()
  expect(callbacks.onClose).not.toHaveBeenCalled()
})

it('does not publish an old error after returning to the source', async () => {
  let fail!: (error: Error) => void
  state.resume.mockReturnValue(new Promise((_resolve, reject) => { fail = reject }))
  show()
  await act(async () => { state.epoch += 2; fail(new Error('Old source error')) })
  expect(screen.queryByText('Old source error')).toBeNull()
  expect(state.register).not.toHaveBeenCalled()
})

it.each(['connection', 'profile', 'offline'])('refuses a stale %s before invoking saved setup', change => {
  if (change === 'connection') {connection.set('other')}
  if (change === 'profile') {profile.set('other')}
  if (change === 'offline') {gateway.set('closed')}
  show()
  expect(state.resume).not.toHaveBeenCalled()
  expect(state.register).not.toHaveBeenCalled()
})
