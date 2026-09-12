import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode, type ComponentProps, type ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => vi.fn())
vi.mock('./canonical-groups', () => ({ canonicalGroupRequest: request }))
vi.mock('./canonical-group-labels', async () => {
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')
  return { useCanonicalGroupLabels: () => ({ ...CANONICAL_GROUP_LOCALES.en, refresh: 'Refresh' }) }
})
vi.mock('@hermes/plugin-sdk', () => ({
  Button: (props: ComponentProps<'button'>) => <button {...props} />,
  Codicon: () => <span />,
  Tip: ({ children }: { children: ReactNode }) => <>{children}</>,
  Dialog: ({ children }: { children: ReactNode }) => <div role="dialog">{children}</div>,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  Switch: ({ checked, disabled, id, onCheckedChange }: {
    checked: boolean; disabled: boolean; id: string; onCheckedChange: (value: boolean) => void
  }) => <input checked={checked} disabled={disabled} id={id} onChange={event => onCheckedChange(event.target.checked)} role="switch" type="checkbox" />
}))

import { CanonicalGroupHome } from './canonical-group-home'

const binding = { connectionId: 'gateway', profile: 'default', roomId: 'room' }
const authority = { gatewayId: 'install:home', epoch: 1 }
const reply = (enabled: boolean) => ({ room_id: 'room', enabled, authority: { gateway_id: authority.gatewayId, epoch: 1 } })
afterEach(() => { cleanup(); request.mockReset() })

it('requires an explicit toggle and uses the exact visible room authority', async () => {
  request.mockImplementation(async (_binding, method, params) => reply(method.endsWith('.set') ? params.enabled : false))
  render(<StrictMode><CanonicalGroupHome binding={binding} authority={authority} name="Team" /></StrictMode>)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Messaging access' }))
  const toggle = await screen.findByRole('switch', { name: 'Allow access from my messaging apps' })
  await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false))
  expect((toggle as HTMLInputElement).checked).toBe(false)
  expect(request.mock.calls.every(call => call[1].endsWith('.get'))).toBe(true)
  fireEvent.click(toggle)
  await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true))
  expect(request).toHaveBeenLastCalledWith(binding, 'groups.control.home.set', {
    room_id: 'room', expected_authority: { gateway_id: authority.gatewayId, epoch: 1 }, enabled: true
  })
})

it('closes on authority change and treats malformed or lost permission receipts as unconfirmed', async () => {
  let finish!: (value: unknown) => void
  request.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const view = render(<CanonicalGroupHome binding={binding} authority={authority} name="Team" />)
  fireEvent.click(screen.getByRole('button', { name: 'Messaging access' }))
  view.rerender(<CanonicalGroupHome binding={binding} authority={{ ...authority, epoch: 2 }} name="Team" />)
  finish(reply(true))
  expect(screen.queryByRole('dialog')).toBeNull()
  request.mockResolvedValue(reply(true))
  fireEvent.click(screen.getByRole('button', { name: 'Messaging access' }))
  await screen.findByRole('alert')
  expect((screen.getByRole('switch') as HTMLInputElement).disabled).toBe(true)
  expect(request.mock.calls.every(call => call[1].endsWith('.get'))).toBe(true)
})
