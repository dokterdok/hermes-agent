// Cross-window mutual exclusion. Web Locks wherever the browser has them: Electron and every
// secure origin, unchanged. A Webapp opened over plain HTTP from another machine has none, and a
// missing API never means "no other window": there a lease in localStorage, which every window of
// the origin shares like the queues and journals it guards, stands in. A lease is dropped when its
// holder finishes or its page goes away, and expires on its own if the holder dies holding it.

const LEASE_PREFIX = 'hermes.desktop.webLockLease.v1:'
const PRESENCE_CHANNEL = 'hermes.desktop.webLockPresence.v1'
// Chrome wakes a hidden tab's timers about once a minute; a live holder renews well inside this.
const LEASE_TTL_MS = 90_000
const RENEW_MS = 15_000
// Two windows that claim in the same instant both write; once this has passed, both read the
// claim that won.
const SETTLE_MS = 50
const RETRY_MS = 100

interface Lease {
  token: string
  until: number
}

const randomHex = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(12)), byte => byte.toString(16).padStart(2, '0')).join('')

const sleep = (ms: number) => new Promise<void>(resolve => window.setTimeout(resolve, ms))
const webLocks = () => (typeof navigator === 'undefined' ? undefined : navigator.locks)

// Lease key -> token this window holds it with. A token per acquisition, not per window, so two
// requests from one window exclude each other exactly as Web Locks do.
const held = new Map<string, string>()
// Other live windows of this origin. Alone, a claim cannot race anyone and skips the settle wait.
const peers = new Set<string>()
let renewTimer: number | undefined

function readLease(key: string): Lease | null {
  try {
    const lease = JSON.parse(window.localStorage.getItem(key) ?? 'null') as null | Partial<Lease>

    return typeof lease?.token === 'string' && typeof lease.until === 'number' ? (lease as Lease) : null
  } catch {
    return null
  }
}

function writeLease(key: string, token: string): void {
  window.localStorage.setItem(key, JSON.stringify({ token, until: Date.now() + LEASE_TTL_MS }))
}

function renewHeld(): void {
  for (const [key, token] of held) {
    const lease = readLease(key)

    try {
      // Never over a live claim of another window's: overwriting it would not stop either task.
      if (!lease || lease.token === token || lease.until <= Date.now()) {
        writeLease(key, token)
      }
    } catch {
      // Storage refused this write; the next tick tries again.
    }
  }

  if (!held.size && renewTimer !== undefined) {
    window.clearInterval(renewTimer)
    renewTimer = undefined
  }
}

// Leases only need renewing while another window could take one over.
function keepRenewing(): void {
  if (held.size && peers.size && renewTimer === undefined) {
    renewTimer = window.setInterval(renewHeld, RENEW_MS)
  }
}

// True once our claim holds (with another window around: once it survived any rival written in
// the same instant), false while someone else holds the lease, null when storage refuses the
// write: then no other window can see the state the lock guards either.
async function claimLease(key: string, token: string): Promise<boolean | null> {
  const lease = readLease(key)

  if (held.has(key) || (lease && lease.until > Date.now())) {
    return false
  }

  try {
    writeLease(key, token)
  } catch {
    return null
  }

  if (peers.size) {
    await sleep(SETTLE_MS)

    if (held.has(key) || readLease(key)?.token !== token) {
      return false
    }
  }

  held.set(key, token)
  keepRenewing()

  return true
}

function releaseLease(key: string, token: string): void {
  if (held.get(key) !== token) {
    return
  }

  held.delete(key)

  try {
    if (readLease(key)?.token === token) {
      window.localStorage.removeItem(key)
    }
  } catch {
    // It expires on its own.
  }
}

if (typeof window !== 'undefined' && typeof BroadcastChannel !== 'undefined' && !webLocks()) {
  const self = randomHex()
  const channel = new BroadcastChannel(PRESENCE_CHANNEL)

  channel.onmessage = ({ data }: MessageEvent<{ from?: unknown; type?: unknown }>) => {
    const from = typeof data?.from === 'string' ? data.from : ''

    if (!from || from === self) {
      return
    }

    if (data.type === 'bye') {
      peers.delete(from)

      return
    }

    if (data.type === 'hello') {
      channel.postMessage({ from: self, type: 'here' })
    }

    if (!peers.has(from)) {
      peers.add(from)
      // Leases taken while alone were never renewed; refresh them before the newcomer claims.
      renewHeld()
      keepRenewing()
    }
  }

  channel.postMessage({ from: self, type: 'hello' })
  // A page restored from the back/forward cache may have missed windows that opened meanwhile.
  window.addEventListener('pageshow', event => event.persisted && channel.postMessage({ from: self, type: 'hello' }))
  // A closing window frees its leases at once. A page kept in that cache may come back, so it
  // keeps them until they expire.
  window.addEventListener('pagehide', event => {
    if (!event.persisted) {
      for (const [key, token] of [...held]) {
        releaseLease(key, token)
      }

      channel.postMessage({ from: self, type: 'bye' })
    }
  })
}

/** `navigator.locks.request(name, task)`: run `task` holding the exclusive cross-window lock
 *  `name`, waiting for it; `signal` aborts the wait, as Web Locks' own `signal` option does. */
export async function withWebLock<T>(name: string, task: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  const locks = webLocks()

  if (locks) {
    return signal ? locks.request(name, { signal }, task) : locks.request(name, task)
  }

  const key = LEASE_PREFIX + name
  const token = randomHex()

  for (;;) {
    signal?.throwIfAborted()
    const claimed = await claimLease(key, token)

    if (claimed === null) {
      return task()
    }

    if (claimed) {
      break
    }

    await sleep(RETRY_MS + Math.random() * RETRY_MS)
  }

  try {
    return await task()
  } finally {
    releaseLease(key, token)
  }
}
