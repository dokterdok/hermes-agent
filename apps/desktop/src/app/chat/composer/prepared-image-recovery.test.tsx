import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { preparedSubmissionKey, writePreparedSubmission } from '@/app/session/hooks/use-prompt-actions/prepared-submissions'
import { captureSubmissionDestination } from '@/app/session/hooks/use-prompt-actions/submission-destination'
import { $notifications, clearNotifications } from '@/store/notifications'

import { PreparedImageRecovery } from './prepared-image-recovery'

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals() })

test('reopened composer offers exact image occurrence only for its original destination', async () => {
  const request = vi.fn()
  const destination = captureSubmissionDestination('original', request)
  const attachments = [{ id: 'image', occurrenceId: 'occurrence', kind: 'image' as const, label: 'image.png', path: '/cache/images/exact.png', mime: 'image/png' }]
  const key = preparedSubmissionKey('original', destination, '  retained image  ', attachments)
  await writePreparedSubmission(key, { id: 'submission', owner: destination.owner, text: 'expanded wire', attachments, params: { session_id: 'original' } })
  const restore = vi.fn()
  const view = render(<PreparedImageRecovery occupied={false} onRestore={restore} request={request} sessionKey="elsewhere" />)
  await waitFor(() => expect(request).not.toHaveBeenCalled())
  expect(screen.queryByRole('button')).toBeNull()
  view.rerender(<PreparedImageRecovery occupied={false} onRestore={restore} request={request} sessionKey="original" />)
  const button = await screen.findByRole('button', { name: 'Restore draft' })
  await act(async () => {fireEvent.click(button)})
  expect(restore).toHaveBeenCalledWith('  retained image  ', attachments)
  expect(request).not.toHaveBeenCalled()
})

test('recovery never overwrites a newer draft or offers an ambiguous legacy send', async () => {
  const request = vi.fn()
  const destination = captureSubmissionDestination('original', request)
  const attachments = [{ id: 'image', kind: 'image' as const, label: 'image.png' }]

  for (const legacyAttempted of [false, true]) {
    await writePreparedSubmission(preparedSubmissionKey('original', destination, String(legacyAttempted), attachments), {
      id: String(legacyAttempted), owner: destination.owner, text: String(legacyAttempted), attachments, params: {}, legacyAttempted
    })
  }

  const restore = vi.fn()
  render(<PreparedImageRecovery occupied onRestore={restore} request={request} sessionKey="original" />)
  const button = await screen.findByRole('button', { name: 'Restore draft' })
  expect(screen.getAllByRole('button')).toHaveLength(1)
  expect((button as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(button)
  expect(restore).not.toHaveBeenCalled()
})

test.each([
  { action: 'switch', fails: false }, { action: 'unmount', fails: false },
  { action: 'switch', fails: true }, { action: 'unmount', fails: true }
])('late atomic recovery cannot restore or report errors into a disposed composer: %j', async ({ action, fails }) => {
  clearNotifications()
  const request = vi.fn()
  const destination = captureSubmissionDestination('original', request)
  const key = preparedSubmissionKey('original', destination, 'retained text', [])
  await writePreparedSubmission(key, { id: 'late-recovery', owner: destination.owner, text: 'retained text', attachments: [], params: { session_id: 'original' } })
  const serialized = localStorage.getItem('hermes.desktop.preparedSubmissions.v1')!
  let release!: (value: boolean) => void
  let reject!: (error: Error) => void
  const compareAndSet = vi.fn(() => new Promise<boolean>((resolve, fail) => {release = resolve; reject = fail}))
  vi.stubGlobal('hermesDesktop', { preparedSubmissions: {
    owner: async () => 'new-window', read: async () => serialized, update: vi.fn(), compareSend: compareAndSet
  } })
  const restore = vi.fn()
  const view = render(<PreparedImageRecovery occupied={false} onRestore={restore} request={request} sessionKey="original" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Restore draft' }))
  await waitFor(() => expect(compareAndSet).toHaveBeenCalledOnce())

  if (action === 'unmount') {view.unmount()}
  else {view.rerender(<PreparedImageRecovery occupied={false} onRestore={restore} request={request} sessionKey="elsewhere" />)}

  await act(async () => {
    if (fails) {reject(new Error('fixture stale claim failure'))}
    else {release(true)}
  })
  expect(restore).not.toHaveBeenCalled()
  expect(request).not.toHaveBeenCalled()
  expect($notifications.get()).toEqual([])
})

test('reads repaired draft storage on Retry without claiming a pending message or overwriting an occupied composer', async () => {
  vi.stubGlobal('hermesDesktop', undefined)
  const request = vi.fn()
  const destination = captureSubmissionDestination('original', request)
  const key = preparedSubmissionKey('original', destination, 'Keep my exact draft', [])
  await writePreparedSubmission(key, {id: 'uncertain-intent', owner: destination.owner, text: 'Keep my exact draft', attachments: [], params: {session_id: 'original', submission_id: 'uncertain-intent'}})
  const storageKey = 'hermes.desktop.preparedSubmissions.v1'
  const saved = localStorage.getItem(storageKey)!
  localStorage.setItem(storageKey, '[]')
  const restore = vi.fn()
  render(<PreparedImageRecovery occupied onRestore={restore} request={request} sessionKey="original" />)
  await screen.findByText('Saved drafts could not be read. Their storage has been left unchanged; try again when it is available.')
  localStorage.setItem(storageKey, saved)
  fireEvent.click(screen.getByRole('button', {name: 'Retry'}))
  const recover = await screen.findByRole('button', {name: 'Restore draft'}) as HTMLButtonElement
  expect(recover.disabled).toBe(true)
  expect(localStorage.getItem(storageKey)).toBe(saved)
  expect(restore).not.toHaveBeenCalled()
  expect(request).not.toHaveBeenCalled()
})

test.each(['switch', 'unmount'] as const)('a repaired journal Retry cannot expose the old destination after %s', async action => {
  vi.stubGlobal('hermesDesktop', undefined)
  const request = vi.fn()
  const destination = captureSubmissionDestination('original', request)
  await writePreparedSubmission(preparedSubmissionKey('original', destination, 'Old destination draft', []), {
    id: 'old-destination', owner: destination.owner, text: 'Old destination draft', attachments: [], params: {session_id: 'original'}
  })
  const storage = localStorage.getItem('hermes.desktop.preparedSubmissions.v1')!
  let resolve!: (value: string) => void
  const read = vi.fn().mockRejectedValueOnce(new Error('storage unavailable')).mockImplementationOnce(() => new Promise<string>(done => {resolve = done})).mockResolvedValue('{}')
  vi.stubGlobal('hermesDesktop', {preparedSubmissions: {read}})
  const restore = vi.fn()
  const view = render(<PreparedImageRecovery occupied={false} onRestore={restore} request={request} sessionKey="original" />)
  fireEvent.click(await screen.findByRole('button', {name: 'Retry'}))
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2))

  if (action === 'unmount') {view.unmount()}
  else {view.rerender(<PreparedImageRecovery occupied={false} onRestore={restore} request={request} sessionKey="elsewhere" />)}

  await act(async () => {resolve(storage)})
  expect(screen.queryByText('Old destination draft')).toBeNull()
  expect(restore).not.toHaveBeenCalled()
  expect(request).not.toHaveBeenCalled()
  expect(localStorage.getItem('hermes.desktop.preparedSubmissions.v1')).toBe(storage)
})
