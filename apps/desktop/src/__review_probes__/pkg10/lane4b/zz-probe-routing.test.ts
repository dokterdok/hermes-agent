// Review probes (claude) for PR #140972 RT-42: the Electron unified-session merge.
// Runs on both trees: on main the prefix option and mergeRegistrySessionRows do
// not exist, so each probe falls back to main's own code path.
import { expect, it } from 'vitest'

import * as routing from '../../../../electron/profile-session-routing'

const r = routing as any

/** main.ts mergeRemoteProfileSessions' registry step, as each tree implements it. */
async function spliceRegistry(sources: any[], params: URLSearchParams, getJson: any, merged: any[], totals: any) {
  if (typeof r.mergeRegistrySessionRows === 'function') {
    return r.mergeRegistrySessionRows(sources, params, getJson, merged, totals) as Promise<number>
  }

  const rows = await r.fetchRegistrySessionRows(sources, params, getJson)

  return r.spliceRegistrySessionRows(merged, rows, totals).added as number
}

const sessions = (n: number, profile: string) =>
  Array.from({ length: n }, (_, i) => ({ id: `s${i}`, profile, last_active: 100000 - i }))

// G3: the same backend reached both as the primary aggregate (base) and as a
// pooled registry source. Base already counted every row; the registry rows are
// all duplicates, so the merged total must not grow.
for (const [label, source, path] of [
  ['shared remote aggregate', { connectionId: 'gw', kind: 'remote', backends: [{ descriptor: 'd', profileLabel: null }] }, '/api/profiles/sessions'],
  ['ssh per-profile backend', { connectionId: 'gw', kind: 'ssh', backends: [{ descriptor: 'd', profileLabel: 'work' }] }, '/api/sessions']
] as const) {
  it(`G3 (${label}): a registry source that duplicates base rows adds nothing to the total`, async () => {
    const all = sessions(650, 'work')
    const limit = 20
    // Base (primary aggregate) holds the first `limit` rows and counts all 650.
    const merged: any[] = all.slice(0, limit).map(row => ({ ...row }))
    const profileTotals: Record<string, number> = { work: 650 }
    let total = 650

    const getJson = async (_descriptor: unknown, requested: string) => {
      const q = new URL(requested, 'http://x').searchParams
      const offset = Number(q.get('offset') || 0)
      const size = Number(q.get('limit') || 20)
      expect(requested.startsWith(path)).toBe(true)
      // An ssh per-profile backend serves its own /api/sessions without a profile stamp.
      const rows = all.slice(offset, offset + size).map(row => (source.kind === 'ssh' ? { id: row.id, last_active: row.last_active } : { ...row }))

      return { sessions: rows, total: 650, profile_totals: { work: 650 } }
    }

    total += await spliceRegistry([source], new URLSearchParams({ limit: String(limit), offset: '0' }), getJson, merged, profileTotals)

    expect(merged).toHaveLength(limit)
    expect(total).toBe(650)
    expect(profileTotals.work).toBe(650)
  })
}

// G6: a deep page needs two primary reads (cap 500). The second read's scan
// errors must reach the composed response, or the renderer treats a partial
// scan as complete.
it('G6: a later primary page that reports a failed profile scan keeps its errors', async () => {
  const params = new URLSearchParams({ profile: 'all', limit: '200', offset: '400' })
  const failure = { profile: 'work', error: 'database is locked' }

  const base = (await r.fetchPrimaryProfileSessions(
    params,
    async (_profile: unknown, requested: string) => {
      const offset = Number(new URL(requested, 'http://x').searchParams.get('offset'))

      if (offset === 0) {
        return { sessions: sessions(500, 'default'), total: 900, profile_totals: { default: 600, work: 300 }, errors: [] }
      }

      // The second read (offset 500, or main's single offset-400 read) loses the work profile.
      return { sessions: sessions(100, 'default'), total: 600, profile_totals: { default: 600 }, errors: [failure] }
    },
    { prefixForMerge: true }
  )) as any

  const response = r.composeUnifiedSessionResponse(base, base.sessions, 400, 200, base.total, base.profile_totals, [])
  expect(response.errors).toEqual([failure])
})
