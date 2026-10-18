import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  applyRegistrySessionTotals,
  fetchRegistrySessionRows,
  spliceRegistrySessionRows
} from '../../../../electron/profile-session-routing'

// RT-42 with two profiles on the overlapping remote registry primary R:
// default has 600 recent sessions, work has 50 older ones. The top-20 window
// holds only default rows, so the work scope has 0 fetched rows => 0 duplicates.
test('probe: an overlapping source profile with no rows in the window is not counted twice', async () => {
  const rows = [
    ...Array.from({ length: 600 }, (_, i) => ({ id: `d${i}`, profile: 'default', started_at: 10_000 - i })),
    ...Array.from({ length: 50 }, (_, i) => ({ id: `w${i}`, profile: 'work', started_at: 100 - i }))
  ]
  const reported = new Map<string, number>()

  const fetched = await fetchRegistrySessionRows(
    [{ connectionId: 'R', kind: 'remote', backends: [{ descriptor: 'remote', profileLabel: null }] }],
    new URLSearchParams({ limit: '20', offset: '0' }),
    async (_d, path) => {
      const p = new URL(path, 'http://x').searchParams
      const start = Number(p.get('offset'))
      const limit = Number(p.get('limit'))
      return { sessions: rows.slice(start, start + limit), total: 650, profile_totals: { default: 600, work: 50 } }
    },
    reported
  )

  // base (primary R) already holds the same window and counted all 650.
  const merged: unknown[] = fetched.map(row => ({ ...(row as object) }))
  const profileTotals: Record<string, number> = { default: 600, work: 50 }
  const before = merged.length
  const { added } = spliceRegistrySessionRows(merged, fetched, profileTotals)
  assert.equal(added, 0)
  const total = 650 + applyRegistrySessionTotals(fetched, merged.slice(before), reported, profileTotals)
  assert.equal(total, 650)
  assert.equal(profileTotals.work, 50)
})
