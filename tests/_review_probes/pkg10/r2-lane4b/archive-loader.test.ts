import { beforeEach, expect, it, vi } from 'vitest'
import { listEveryArchivedSession } from '@/store/sidebar-archive'
import {
  composeUnifiedSessionResponse,
  fetchAllProfilesSessionsPerProfile,
  fetchPrimaryProfileSessions,
} from '../../../../apps/desktop/electron/profile-session-routing'

const listAllProfileSessions = vi.hoisted(() => vi.fn())
vi.mock('@/api/sessions', () => ({ listAllProfileSessions }))
beforeEach(() => listAllProfileSessions.mockReset())

it('the real Archived loader retains every chat when the oldest is pinned', async () => {
  const archived = Array.from({ length: 1001 }, (_, i) => ({
    id: `archive-${i}`, profile: 'default', archived: true, pinned: i === 1000, last_active: 2000 - i,
  }))
  listAllProfileSessions.mockImplementation(async (limit, _min, archivedMode, _order, profile, _filters, offset) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset), profile, archived: archivedMode })
    const base = await fetchPrimaryProfileSessions(params, async (_profile, path) => {
      const params = new URL(path, 'http://fixture').searchParams
      const from = Number(params.get('offset'))
      const cap = Number(params.get('limit'))
      return {
        sessions: [...archived.slice(from, from + cap), ...archived.slice(from + cap).filter(row => row.pinned)],
        total: archived.length, profile_totals: { default: archived.length },
      }
    }, { prefixForMerge: true })
    const merged = [...base.sessions].sort((a: any, b: any) => b.last_active - a.last_active)
    return composeUnifiedSessionResponse(base, merged, offset, limit, base.total, base.profile_totals, [], { archivedOnly: true })
  })
  const rows = await listEveryArchivedSession()
  expect(rows.map(row => row.id)).toContain('archive-999')
  expect(rows).toHaveLength(1001)
})

it('the real Archived loader sees the old archive through native per-profile aggregation', async () => {
  const old = { id: 'old', profile: 'default', archived: true, last_active: 1 }
  const pins = Array.from({ length: 500 }, (_, i) => ({
    id: `pin-${i}`, profile: 'default', archived: false, pinned: true, last_active: 1000 - i,
  }))
  listAllProfileSessions.mockImplementation(async (limit, _min, archivedMode, _order, profile, _filters, offset) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset), profile, archived: archivedMode })
    return fetchAllProfilesSessionsPerProfile(`/api/profiles/sessions?${params}`, ['default'], async (_profile, path) => {
      const query = new URL(path, 'http://fixture').searchParams
      const from = Number(query.get('offset'))
      const cap = Number(query.get('limit'))
      return { sessions: [...[old].slice(from, from + cap), ...pins], total: 1, profile_totals: { default: 1 } }
    })
  })
  const rows = await listEveryArchivedSession()
  expect(rows.some(row => row.id === 'old')).toBe(true)
})
