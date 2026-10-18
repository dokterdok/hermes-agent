// Review probe (claude) for PR #140972 G4: the full Archived load (renderer
// pager) over the Electron unified merge, with enough live pins to fill a page.
import { beforeEach, expect, it, vi } from 'vitest'

import * as routing from '../../../../electron/profile-session-routing'

const listAllProfileSessions = vi.hoisted(() => vi.fn())
vi.mock('@/api/sessions', () => ({ listAllProfileSessions }))

const { listEveryArchivedSession } = await import('@/store/sidebar-archive')
const r = routing as any

const archived = [{ id: 'a-old', profile: 'default', archived: true, pinned: false, last_active: 1 }]
const pins = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ id: `pin-${i}`, profile: 'default', archived: false, pinned: true, last_active: 1000 + i }))

// The PR's backend (profiles.py archived=only): archived window, then every live pin.
const headBackend = (livePins: any[]) => (offset: number, limit: number) => ({
  sessions: [...archived.slice(offset, offset + limit), ...livePins],
  total: archived.length,
  profile_totals: { default: archived.length }
})

// main's backend: one recency window over archived rows AND live pins (_pinned_window).
const mainBackend = (livePins: any[]) => (offset: number, limit: number) => {
  const merged = [...livePins, ...archived].sort((a, b) => b.last_active - a.last_active)
  const window = merged.slice(offset, offset + limit)

  return {
    sessions: [...window, ...merged.slice(offset + limit).filter(row => row.pinned)],
    total: archived.length,
    profile_totals: { default: archived.length }
  }
}

/** Electron mergeRemoteProfileSessions with only the primary aggregate in play. */
function viaElectronMerge(backend: (offset: number, limit: number) => any) {
  return async (limit: number, offset: number) => {
    const params = new URLSearchParams({ profile: 'all', limit: String(limit), offset: String(offset), archived: 'only' })

    const base = await r.fetchPrimaryProfileSessions(
      params,
      async (_profile: unknown, path: string) => {
        const q = new URL(path, 'http://x').searchParams

        return backend(Number(q.get('offset')), Number(q.get('limit')))
      },
      { prefixForMerge: true }
    )

    const merged = [...base.sessions].sort((a: any, b: any) => b.last_active - a.last_active)

    return r.composeUnifiedSessionResponse(base, merged, offset, limit, base.total, base.profile_totals, [])
  }
}

beforeEach(() => listAllProfileSessions.mockReset())

const wire = (page: (limit: number, offset: number) => Promise<any> | any) =>
  listAllProfileSessions.mockImplementation(async (...args: any[]) => page(args[0], args[6]))

for (const [name, backend] of [['head backend', headBackend], ['main backend', mainBackend]] as const) {
  it(`G4 (${name}, Electron merge): 200 live pins do not hide the one archived chat`, async () => {
    wire(viaElectronMerge(backend(pins(200))))
    const loaded = await listEveryArchivedSession()
    expect(loaded.filter(s => s.archived).map(s => s.id)).toEqual(['a-old'])
  })

  it(`G4 control (${name}, Electron merge): 199 live pins`, async () => {
    wire(viaElectronMerge(backend(pins(199))))
    const loaded = await listEveryArchivedSession()
    expect(loaded.filter(s => s.archived).map(s => s.id)).toEqual(['a-old'])
  })

  it(`G4 control (${name}, direct backend, no merge): 200 live pins`, async () => {
    wire((limit, offset) => backend(pins(200))(offset, limit))
    const loaded = await listEveryArchivedSession()
    expect(loaded.filter(s => s.archived).map(s => s.id)).toEqual(['a-old'])
  })
}
