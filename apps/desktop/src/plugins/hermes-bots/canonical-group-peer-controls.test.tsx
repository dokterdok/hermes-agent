import type * as HermesSdk from '@hermes/plugin-sdk'
// eslint-disable-next-line no-restricted-imports -- Exercise the same serialized errors as the shared client.
import { jsonRpcErrorFromFrame } from '@hermes/shared'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { answerGroupRequests, CANONICAL_GROUP_CAPABILITIES, DISBAND_FAILURES, localGatewaySdkMock } from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { $canonicalGroupBindings, registerCanonicalGroup } from './canonical-group-registry'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'
import wire from './fixtures/peer-control-responses.json'
import { $groupChats, updateGroupChat } from './group-chat'
import { translateBotsIn } from './i18n-test-helper'

const { request, locale } = vi.hoisted(() => ({ request: vi.fn(), locale: { value: 'en' } }))
vi.mock('@hermes/plugin-sdk', async importOriginal =>
  localGatewaySdkMock(await importOriginal<typeof HermesSdk>(), request, () => translateBotsIn(locale.value)))

// Captured through AuthorityConnection.dispatch on the merged gateway. The fixture generator
// verifies the durable receipt is absent/present; neither state exposes that receipt to Desktop.
// Typed Disband reasons are injected at the service boundary, then serialized by the dispatcher.
// The untyped frame is ws.py's dispatch-crash reply on the current gateway.
const frame = (name: keyof typeof wire) => JSON.parse(wire[name])
const binding = { connectionId: 'local', profile: 'default', roomId: 'linked' }
const locales = Object.keys(CANONICAL_GROUP_LOCALES) as (keyof typeof CANONICAL_GROUP_LOCALES)[]
const originalDesktop = window.hermesDesktop

beforeEach(() => {
  Object.defineProperty(window, 'hermesDesktop', { configurable: true, writable: true, value: undefined })
  $canonicalGroupBindings.set({})
  $groupChats.set({})
})
afterEach(() => {
  cleanup()
  request.mockReset()
  localStorage.clear()
  window.hermesDesktop = originalDesktop
})

function gateway(state: () => unknown, control: (method: string) => unknown) {
  answerGroupRequests(request, { local: method => ({
    'groups.capabilities': CANONICAL_GROUP_CAPABILITIES, 'groups.state': state(), 'groups.log': { events: [] }
  } as Record<string, unknown>)[method] ?? control(method) })
}

const discardCases = locales.flatMap(language => (['withoutReceipt', 'withReceipt'] as const).flatMap(state => [
  { language, state, refusal: 'stopPending' as const, message: 'discardStopPending' as const },
  { language, state, refusal: 'unreachable' as const, message: 'discardPeerUnreachable' as const }
]))

it.each(discardCases)('offers Discard $state and keeps the deferred turn on $refusal ($language)', async ({ language, state, refusal, message }) => {
  locale.value = language
  const labels = CANONICAL_GROUP_LOCALES[language]
  let snapshot = frame(state).result
  let release!: (value: unknown) => void
  gateway(() => snapshot, method => {
    if (method === 'groups.discard') {return new Promise(resolve => { release = resolve }).then(value => {
      const response = JSON.parse(value as string)

      if (response.error) {throw jsonRpcErrorFromFrame(response.error)}
      snapshot = frame('afterDiscard').result

      return response.result
    })}

    return undefined
  })
  render(<CanonicalGroupWorkspace binding={binding} />)
  const discard = await screen.findByRole('button', { name: 'Discard' })
  expect((discard as HTMLButtonElement).disabled).toBe(false)
  expect(screen.getByText(labels.pendingDiscard.replace('{name}', 'reviewer'))).toBeTruthy()
  fireEvent.click(discard)
  const confirm = within(screen.getByRole('alertdialog', { name: labels.discardUnknown }))
  expect(confirm.getByText(labels.discardWarning)).toBeTruthy()
  fireEvent.click(confirm.getByRole('button', { name: labels.confirmDiscard }))
  await waitFor(() => expect((discard as HTMLButtonElement).disabled).toBe(true))
  await act(async () => release(wire[refusal]))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', labels[message])
  expect((discard as HTMLButtonElement).disabled).toBe(false)
  // A retry of Discard keeps the exact turn; only a confirmed result and refreshed state remove it.
  fireEvent.click(discard)
  fireEvent.click(screen.getByRole('button', { name: labels.confirmDiscard }))
  await act(async () => release(wire.discarded))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull())
})

const disbandCases = locales.flatMap(language => DISBAND_FAILURES.map(failure => ({ language, ...failure })))

it.each(disbandCases)('keeps the group chat and explains Disband $name ($language)', async ({ language, error, detail }) => {
  locale.value = language
  const labels = CANONICAL_GROUP_LOCALES[language]
  const room = frame('withReceipt').result.room
  const onBack = vi.fn()
  const key = registerCanonicalGroup(binding, room)
  updateGroupChat(room.name, record => ({ ...record, roomId: binding.roomId, engine: { kind: 'gateway', ...binding } }), { sync: false })
  const original = structuredClone($groupChats.get()[room.name])
  gateway(() => frame('withReceipt').result, method => {
    if (method === 'groups.disband') {throw error}

    return undefined
  })
  render(<CanonicalGroupWorkspace binding={binding} onBack={onBack} />)
  const disband = translateBotsIn(language)('group.disbandAction') as string
  fireEvent.click(await screen.findByRole('button', { name: disband }))
  const dialog = within(await screen.findByRole('dialog'))
  fireEvent.click(dialog.getByRole('button', { name: disband }))
  expect(await dialog.findByText(labels.disbandKept.replace('{reason}', labels[detail]))).toBeTruthy()
  expect((dialog.getByRole('button', { name: disband }) as HTMLButtonElement).disabled).toBe(false)
  expect($canonicalGroupBindings.get()[key]).toMatchObject(binding)
  expect($groupChats.get()[room.name]).toEqual(original)
  expect(onBack).not.toHaveBeenCalled()
})
