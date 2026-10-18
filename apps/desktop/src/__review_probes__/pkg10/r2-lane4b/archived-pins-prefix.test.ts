import assert from 'node:assert/strict'

import { test } from 'vitest'

import { composeUnifiedSessionResponse, fetchPrimaryProfileSessions } from '../../../../electron/profile-session-routing'

// Modern backend /api/profiles/sessions?archived=only (profiles.py:507-511):
// window = archived[offset:offset+limit] + every PINNED archived row past it
// (_pinned_window), then live pins. 1500 archived rows; 20 of them (positions
// 1200..1219) are also pinned. No live pins at all.
const N = 1500
const rows = Array.from({ length: N }, (_, i) => ({
  id: `a${i}`,
  profile: 'default',
  archived: true,
  pinned: i >= 1200 && i < 1220,
  last_active: 100_000 - i,
  started_at: 100_000 - i
}))

async function backend(_p: unknown, path: string) {
  const q = new URL(path, 'http://x').searchParams
  const offset = Number(q.get('offset'))
  const limit = Number(q.get('limit'))
  const window = rows.slice(offset, offset + limit)
  window.push(...rows.slice(offset + limit).filter(r => r.pinned))

  return { sessions: window.map(r => ({ ...r })), total: N, profile_totals: { default: N }, errors: [] }
}

// mergeRemoteProfileSessions (main.ts) without remotes/registry rows.
async function mergedPage(offset: number, limit: number) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), archived: 'only', order: 'recent', profile: 'all' })
  const base: any = await fetchPrimaryProfileSessions(params, backend as any, { prefixForMerge: true })
  const merged = [...base.sessions].sort((a: any, b: any) => b.last_active - a.last_active)

  return composeUnifiedSessionResponse(base, merged, offset, limit, Number(base.total) || 0, { ...base.profile_totals }, [], {
    archivedOnly: true
  } as any) as any
}

test('probe: Electron merge path Archived loader sees every archived row when some archived rows are pinned', async () => {
  // listEveryArchivedSession's loop (sidebar-archive.ts), page size 500.
  const seen = new Set<string>()
  let archivedCount = 0
  let offset = 0

  for (let guard = 0; guard < 20; guard++) {
    const page = await mergedPage(offset, 500)
    const before = archivedCount

    for (const s of page.sessions) {
      if (!seen.has(s.id)) {
        seen.add(s.id)
        archivedCount += s.archived ? 1 : 0
      }
    }

    offset += 500

    if (page.sessions.length === 0 || archivedCount >= page.total || (offset >= page.total && archivedCount === before)) {
      break
    }
  }

  const missing = rows.filter(r => !seen.has(r.id)).map(r => r.id)
  assert.deepEqual(missing.slice(0, 3), [])
  assert.equal(archivedCount, N)
})
