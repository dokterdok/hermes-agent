"""Independent lane4b probes: execute the real TypeScript routing exports in Node."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
MODULE = (ROOT / "apps/desktop/electron/profile-session-routing.ts").as_uri()


def node_probe(body):
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e",
         f"import assert from 'node:assert/strict'; import * as routing from '{MODULE}';\n" + body],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_does_not_recount_primary_scope():
    node_probe("""
const rows = Array.from({length: 650}, (_, i) => ({id: `s${i}`, profile: 'default', last_active: 1000-i}));
const params = new URLSearchParams({limit: '20', offset: '0', profile: 'all'});
const page = {sessions: rows.slice(0, 20), total: 650, profile_totals: {default: 650}};
const base = await routing.fetchPrimaryProfileSessions(params, async () => structuredClone(page), {prefixForMerge: true});
const merged = [...base.sessions], totals = {...base.profile_totals};
const sources = [{connectionId: 'remote-a', kind: 'remote', backends: [{descriptor: 'a', profileLabel: null}]}];
let contribution;
if (routing.mergeRegistrySessionRows) {
  contribution = await routing.mergeRegistrySessionRows(sources, params, async () => structuredClone(page), merged, totals);
} else {
  // Exact pre-PR call chain, before the helper was introduced.
  const fetched = await routing.fetchRegistrySessionRows(sources, params, async () => structuredClone(page));
  contribution = routing.spliceRegistrySessionRows(merged, fetched, totals).added;
}
assert.equal(merged.length, 20);
assert.equal(base.total + contribution, 650, 'one backend/profile must be counted once');
assert.equal(totals.default, 650);
""")


def test_later_profile_page_errors_are_preserved():
    node_probe("""
const rows = Array.from({length: 650}, (_, i) => ({id: `s${i}`, profile: 'work', last_active: 1000-i}));
const result = await routing.fetchAllProfilesSessionsPerProfile(
 '/api/profiles/sessions?profile=all&limit=200&offset=400', ['work'], async (_p, path) => {
   const p = new URL(path, 'http://test').searchParams;
   const offset = Number(p.get('offset')), limit = Number(p.get('limit'));
   return offset === 0
     ? {sessions: rows.slice(0, limit), total: 650, profile_totals: {work: 650}, errors: []}
     : {sessions: [], total: 0, profile_totals: {}, errors: [{profile: 'work', error: 'database is locked'}]};
 });
assert.deepEqual(result.errors, [{profile: 'work', error: 'database is locked'}], 'later-page scan failure must reach renderer');
""")


def test_primary_prefix_reports_its_later_failure():
    node_probe("""
let failed = false;
const rows = Array.from({length: 650}, (_, i) => ({id: `s${i}`, profile: 'work'}));
const result = await routing.fetchPrimaryProfileSessions(
 new URLSearchParams({profile: 'all', limit: '200', offset: '400'}), async (_p, path) => {
   const p = new URL(path, 'http://test').searchParams;
   const offset = Number(p.get('offset')), limit = Number(p.get('limit'));
   if (offset === 500) {
     failed = true;
     return {sessions: [], total: 0, profile_totals: {}, errors: [{profile: 'work', error: 'database is locked'}]};
   }
   return {sessions: rows.slice(offset, offset+limit), total: 650, profile_totals: {work: 650}, errors: []};
 }, {prefixForMerge: true});
assert.equal(!failed || result.errors?.some(e => e.profile === 'work'), true, 'no scan failure may be reported as success');
""")


def test_same_id_on_two_connections_survives_splice():
    node_probe("""
const a = {id: 'copied-history', profile: 'default', connection_id: 'host-a'};
const b = {id: 'copied-history', profile: 'default', connection_id: 'host-b'};
const merged = [a];
routing.spliceRegistrySessionRows(merged, [b], {default: 1});
assert.deepEqual(merged.map(r => r.connection_id), ['host-a', 'host-b'], 'independent hosts retain their copied history');
""")


def test_profile_alias_does_not_change_a_pin_identity_mid_paging():
    node_probe("""
// The flat /api/sessions door has no profile labels. A per-profile SSH
// source tags each returned row only after all its pages were accumulated.
const rows = Array.from({length: 150}, (_, i) => ({id: `s${i}`, pinned: i === 125}));
const result = await routing.fetchRegistrySessionRows(
 [{connectionId: 'ssh-a', kind: 'ssh', backends: [{descriptor: 'a', profileLabel: 'work'}]}],
 new URLSearchParams({limit: '150', offset: '0'}), async (_d, path) => {
   const p = new URL(path, 'http://test').searchParams;
   const offset = Number(p.get('offset')), limit = Number(p.get('limit'));
   const window = rows.slice(offset, offset+limit);
   return {sessions: [...window, ...rows.filter(r => r.pinned && !window.includes(r))], total: 150};
 });
assert.equal(result.length, 150);
assert.equal(result.filter(r => r.id === 's125').length, 1);
assert.ok(result.every(r => r.profile === 'work'));
""")
