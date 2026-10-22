import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async () => {
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')

  return { host: { requestProfile: captureGroupRequests(request).request } }
})

import { offersGroupMethod, useGroupMethods } from './canonical-group-capabilities'
import { STANDALONE_GROUP_CAPABILITIES } from './canonical-group-contract-test-utils'

const route = { connectionId: 'remote', profile: 'team' }
afterEach(() => { vi.useRealTimers(); request.mockReset() })

it('offers no control after a failed capabilities read, and reads again while the room is visible', async () => {
  vi.useFakeTimers()
  request.mockRejectedValueOnce(new Error('request timed out')).mockResolvedValue(STANDALONE_GROUP_CAPABILITIES)
  const { result, rerender } = renderHook(({ visible }) => useGroupMethods(route, visible), { initialProps: { visible: false } })
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(request).not.toHaveBeenCalled()

  rerender({ visible: true })
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(request).toHaveBeenCalledOnce()
  expect(['groups.discard', 'groups.disband', 'groups.retry'].map(method => offersGroupMethod(result.current, method))).toEqual([false, false, false])

  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(request).toHaveBeenCalledTimes(2)
  expect(['groups.discard', 'groups.disband', 'groups.retry'].map(method => offersGroupMethod(result.current, method))).toEqual([false, true, true])

  // An answer is kept for the room view's lifetime.
  rerender({ visible: false })
  rerender({ visible: true })
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(request).toHaveBeenCalledTimes(2)
})
