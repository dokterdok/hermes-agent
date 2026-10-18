import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const routing = await import(pathToFileURL(path.join(process.cwd(), 'apps/desktop/electron/profile-session-routing.ts')))
const paramsAt = offset => new URLSearchParams({ limit: '500', offset: String(offset), profile: 'all', archived: 'only' })
const old = { id: 'old', profile: 'default', archived: true, pinned: false, last_active: 1 }
const pins = count => Array.from({ length: count }, (_, i) => ({
  id: `pin-${i}`, profile: 'default', archived: false, pinned: true, last_active: 2000 - i,
}))
const paging = url => {
  const p = new URL(url, 'http://fixture').searchParams
  return { offset: Number(p.get('offset')), limit: Number(p.get('limit')) }
}

// The renderer Archived loader's stop contract, with its real 500-row page size.
async function loadArchive(fetch) {
  let archivedCount = 0
  const seen = new Set()
  const rows = []
  for (let offset = 0; offset < 10000; offset += 500) {
    const page = await fetch(offset)
    const before = archivedCount
    for (const row of page.sessions) {
      if (!seen.has(row.id)) {
        seen.add(row.id)
        rows.push(row)
        archivedCount += row.archived ? 1 : 0
      }
    }
    if (!page.sessions.length || archivedCount >= page.total || (offset + 500 >= page.total && archivedCount === before)) return rows
  }
  throw new Error('Archive loader failed to terminate')
}

const cases = {
  async unseen_profile_total() {
    const defaultRows = Array.from({ length: 20 }, (_, i) => ({ id: `d${i}`, profile: 'default', started_at: 100 - i }))
    const base = { sessions: defaultRows, total: 670, profile_totals: { default: 20, work: 650 } }
    const merged = defaultRows.map(row => ({ ...row }))
    const totals = { ...base.profile_totals }
    const contribution = await routing.mergeRegistrySessionRows(
      [{ connectionId: 'R', kind: 'remote', backends: [{ descriptor: 'R', profileLabel: null }] }],
      new URLSearchParams({ limit: '20', offset: '0' }),
      async () => ({ ...base, sessions: defaultRows.map(row => ({ ...row })) }), merged, totals,
    )
    console.log(JSON.stringify({ total: base.total + contribution, totals }))
    assert.equal(base.total + contribution, 670, 'Same primary/registry host must count the older work profile once')
    assert.equal(totals.work, 650)
  },
  async native_archive() {
    const live = pins(500)
    const rows = await loadArchive(offset => routing.fetchAllProfilesSessionsPerProfile(
      `/api/profiles/sessions?${paramsAt(offset)}`, ['default'], async (_profile, url) => {
        const { offset, limit } = paging(url)
        return { sessions: [...[old].slice(offset, offset + limit), ...live], total: 1, profile_totals: { default: 1 } }
      },
    ))
    console.log(JSON.stringify({ archived: rows.filter(row => row.archived).map(row => row.id), rows: rows.length }))
    assert.ok(rows.some(row => row.id === 'old'), 'Native per-profile aggregate must not hide the archive behind live pins')
  },
  async partial_overlap_total() {
    const base = Array.from({ length: 20 }, (_, i) => ({ id: `a-${i}`, profile: 'default', connection_id: 'A', started_at: 1000 - i }))
    const remote = Array.from({ length: 650 }, (_, i) => ({ id: i === 0 ? 'a-0' : `b-${i}`, profile: 'default', started_at: 900 - i }))
    const merged = [...base]
    const totals = { default: 20 }
    const contribution = await routing.mergeRegistrySessionRows(
      [{ connectionId: 'B', kind: 'remote', backends: [{ descriptor: 'B', profileLabel: null }] }],
      new URLSearchParams({ limit: '20', offset: '0' }),
      async () => ({ sessions: remote.slice(0, 20), total: remote.length, profile_totals: { default: remote.length } }),
      merged, totals,
    )
    console.log(JSON.stringify({ total: 20 + contribution, loaded: merged.length, totals }))
    // Preserve the PRE-EXISTING profile+id dedupe of the single colliding row.
    // The round-2 fix must not discard the 630 additional noncolliding rows' total.
    assert.equal(20 + contribution, 669, 'One colliding ID must not collapse the entire other gateway total to its fetched prefix')
  },
  async legacy_pin_prefix() {
    const backend = [...pins(1000), old]
    const offsets = []
    const rows = await loadArchive(async offset => {
      const base = await routing.fetchPrimaryProfileSessions(paramsAt(offset), async (_profile, url) => {
        const { offset, limit } = paging(url)
        offsets.push(offset)
        return {
          sessions: [...backend.slice(offset, offset + limit), ...backend.slice(offset + limit).filter(row => row.pinned)],
          total: 1, profile_totals: { default: 1 },
        }
      }, { prefixForMerge: true })
      const merged = [...base.sessions].sort((a, b) => b.last_active - a.last_active)
      return routing.composeUnifiedSessionResponse(base, merged, offset, 500, 1, base.profile_totals, [], { archivedOnly: true })
    })
    console.log(JSON.stringify({ archived: rows.filter(row => row.archived).map(row => row.id), offsets }))
    assert.ok(rows.some(row => row.id === 'old'), 'A page consisting of already backfilled pins is not backend exhaustion')
  },
  async archived_pin_gap() {
    const archived = Array.from({ length: 1001 }, (_, i) => ({
      id: `archive-${i}`, profile: 'default', archived: true, pinned: i === 1000, last_active: 2000 - i,
    }))
    const reads = []
    const rows = await loadArchive(async offset => {
      const base = await routing.fetchPrimaryProfileSessions(paramsAt(offset), async (_profile, url) => {
        const { offset, limit } = paging(url)
        reads.push([offset, limit])
        return {
          sessions: [...archived.slice(offset, offset + limit), ...archived.slice(offset + limit).filter(row => row.pinned)],
          total: archived.length, profile_totals: { default: archived.length },
        }
      }, { prefixForMerge: true })
      const merged = [...base.sessions].sort((a, b) => b.last_active - a.last_active)
      return routing.composeUnifiedSessionResponse(base, merged, offset, 500, archived.length, base.profile_totals, [], { archivedOnly: true })
    })
    const missing = archived.filter(row => !rows.some(received => received.id === row.id)).map(row => row.id)
    console.log(JSON.stringify({ loaded: rows.length, missing, reads }))
    assert.equal(rows.length, archived.length, 'An archived pin returned as backfill must not consume an unread cursor position')
  },
  async later_errors() {
    const rows = Array.from({ length: 650 }, (_, i) => ({ id: `s${i}`, profile: 'default' }))
    const result = await routing.fetchPrimaryProfileSessions(new URLSearchParams({ limit: '200', offset: '400' }), async (_p, url) => {
      const { offset, limit } = paging(url)
      return { sessions: rows.slice(offset, offset + limit), total: 650, errors: offset ? [{ profile: 'work', error: 'locked' }] : [] }
    }, { prefixForMerge: true })
    assert.deepEqual(result.errors, [{ profile: 'work', error: 'locked' }])
  },
}
await cases[process.argv[2]]()
