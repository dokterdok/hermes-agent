import { afterEach, expect, it, vi } from 'vitest'

// Windows of one plain-HTTP origin: separate module instances that share localStorage and the
// presence channel, with no Web Locks (jsdom has none, like an insecure origin).
async function openWindow() {
  vi.resetModules()

  return import('./web-locks')
}

const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

afterEach(() => window.localStorage.clear())

it('keeps a lock exclusive across windows without Web Locks, and frees a dead holder once its lease expires', async () => {
  expect(navigator.locks).toBeUndefined()
  const [a, b] = [await openWindow(), await openWindow()]
  await pause(20) // each window learns of the other
  const order: string[] = []
  let release!: () => void

  const first = a.withWebLock('drain', () => new Promise<void>(resolve => {
    order.push('a')
    release = resolve
  }))

  await vi.waitFor(() => expect(order).toEqual(['a']))
  const second = b.withWebLock('drain', async () => void order.push('b'))
  await pause(300)
  expect(order).toEqual(['a'])
  release()
  await Promise.all([first, second])
  expect(order).toEqual(['a', 'b'])

  window.localStorage.setItem('hermes.desktop.webLockLease.v1:drain', JSON.stringify({ token: 'dead', until: Date.now() - 1 }))
  await expect(b.withWebLock('drain', async () => 'ran')).resolves.toBe('ran')
})

it('lets one of two windows that claim in the same instant run at a time', async () => {
  const [a, b] = [await openWindow(), await openWindow()]
  await pause(20)
  const read = Storage.prototype.getItem
  let stale = 1

  // Window b reads before window a's claim has reached it: both write, and the settle re-read decides.
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
    const value = read.call(this, key)

    if (stale && value && key.endsWith(':race')) {
      stale = 0

      return null
    }

    return value
  })

  let running = 0
  let overlapped = false

  const task = async () => {
    running += 1
    overlapped ||= running > 1
    await pause(30)
    running -= 1
  }

  await Promise.all([a.withWebLock('race', task), b.withWebLock('race', task)])
  expect(stale).toBe(0)
  expect(overlapped).toBe(false)
})
