"""Run UI behavior probes against real Desktop modules, through Vitest.

The generated Vitest harness stays beside this untracked Python entrypoint.
No production source is copied or inspected by the tests.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent

CONFIG = r"""
import { defineConfig } from 'vitest/config'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const here = path.dirname(fileURLToPath(import.meta.url))
const app = path.resolve(here, '../../../../apps/desktop')
export default defineConfig({
 root: app,
 test: { maxWorkers: 1, projects: [{
   extends: path.join(app, 'vite.config.ts'),
   test: {
     name: 'review', environment: 'jsdom', globals: true,
     setupFiles: [path.join(app, 'vitest.setup.ts')],
     include: [path.join(here, 'test_zz_probe_ui.test.tsx').replaceAll('\\', '/')],
     testTimeout: 15000, hookTimeout: 30000, env: {NODE_ENV: 'development'}
   }
 }] }
})
"""

UI = r"""
import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, render } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { $canonicalGroupBindings } from '@/plugins/hermes-bots/canonical-group-registry'
import { $botMeta } from '@/plugins/hermes-bots/data'
import { $groupChats, $groupChatWorkspace } from '@/plugins/hermes-bots/group-chat'
import { GroupChatWorkspace } from '@/plugins/hermes-bots/group-chat-view'
import { translateBots } from '@/plugins/hermes-bots/i18n-test-helper'
import { listEveryArchivedSession } from '@/store/sidebar-archive'
import * as routing from '../../../../apps/desktop/electron/profile-session-routing'

const { request, listAllProfileSessions } = vi.hoisted(() => ({ request: vi.fn(), listAllProfileSessions: vi.fn() }))
vi.mock('@/api/sessions', async importOriginal => ({
 ...await importOriginal(), listAllProfileSessions
}))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
 const sdk = await importOriginal<typeof HermesSdk>()
 const {en} = await import('@/i18n/en')
 return { ...sdk, gatewayActivationEpoch: () => 1,
   host: { ...sdk.host, requestProfile: request,
     request: (method, params) => request(null, method, params),
     notify: vi.fn(), openWorkspace: vi.fn(() => () => undefined), connections: vi.fn(async () => []),
     state: {...sdk.host.state, connectionId: sdk.atom('a'), profile: sdk.atom('default'), gateway: sdk.atom('open')}
   }, useI18n: () => ({locale: 'en', t: en}), usePluginI18n: () => translateBots
 }
})
vi.mock('@/plugins/hermes-bots/group-chat-parts', async importOriginal => ({
 ...await importOriginal(), GroupImageControls: () => null
}))
const state = host.state as any
const members = [{member_id: 'alpha', profile: 'alpha', handle: 'alpha'}, {member_id: 'beta', profile: 'beta', handle: 'beta'}]
const room = (source: string) => ({room_id: `room-${source}`, name: 'Team', members})
const roster = [{name: 'alpha'}, {name: 'beta'}]

beforeEach(() => {
 state.connectionId.set('a'); state.profile.set('default'); state.gateway.set('open')
 $canonicalGroupBindings.set({}); $groupChats.set({}); $groupChatWorkspace.set(null); $botMeta.set({})
 request.mockReset(); listAllProfileSessions.mockReset()
 Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks() })

function gateway(list: (route: any) => unknown) {
 request.mockImplementation(async (route, method, params) => {
   if (method === 'groups.capabilities') return {driver: true, persistent_process: true}
   if (method === 'groups.list') return list(route)
   if (method === 'groups.state') return {room: {...room(route.connectionId), latest_seq: 0}, driver_status: {}}
   if (method === 'groups.log') return {events: []}
   throw new Error(`Unexpected ${method}`)
 })
}

it('cancelled_lookup_does_not_bind_old_connection', async () => {
 let releaseA: ((value: any) => void) | undefined
 gateway(route => route.connectionId === 'a'
   ? new Promise(resolve => {releaseA = resolve})
   : new Promise(() => {}))
 await act(async () => {render(<GroupChatWorkspace group="Team" members={roster} />)})
 await act(async () => {state.connectionId.set('b')})
 await act(async () => {releaseA?.({rooms: [room('a')], next_offset: null})})
 expect($canonicalGroupBindings.get().Team?.connectionId).not.toBe('a')
 expect(request.mock.calls.filter(([route, method]) => method === 'groups.state' && route.connectionId === 'a')).toHaveLength(0)
})

it('restored_key_uses_its_owner_when_foreground_disconnected', async () => {
 state.connectionId.set('b'); state.gateway.set('closed')
 gateway(route => ({rooms: [room(route.connectionId)], next_offset: null}))
 await act(async () => {render(<GroupChatWorkspace group="canonical:a:default:room-a" members={[]} />)})
 expect(request.mock.calls.find(([route, method]) => method === 'groups.state' && route.connectionId === 'a')?.[2])
   .toMatchObject({room_id: 'room-a'})
})

it('name_binding_is_scoped_after_a_completed_source_switch', async () => {
 gateway(route => ({rooms: [room(route.connectionId)], next_offset: null}))
 let view: ReturnType<typeof render>
 await act(async () => {view = render(<GroupChatWorkspace group="Team" members={roster} />)})
 view!.unmount()
 await act(async () => {state.connectionId.set('b')})
 request.mockClear()
 await act(async () => {render(<GroupChatWorkspace group="Team" members={roster} />)})
 expect(request.mock.calls.filter(([route, method]) => method === 'groups.state' && route.connectionId === 'a')).toHaveLength(0)
 expect($canonicalGroupBindings.get().Team?.connectionId).not.toBe('a')
})

it('archive_loader_reaches_archive_behind_200_live_pins', async () => {
 const archived = {id: 'old-archive', profile: 'default', archived: true, last_active: 1}
 const pins = Array.from({length: 200}, (_, i) => ({id: `pin${i}`, profile: 'default', archived: false, pinned: true, last_active: 1000-i}))
 listAllProfileSessions.mockImplementation(async (limit, _min, _archived, _order, _scope, _extra, offset) => {
   const params = new URLSearchParams({profile: 'all', limit: String(limit), offset: String(offset), archived: 'only'})
   // Current backend contract: archive-only cursor plus live-pin backfill.
   const base = await routing.fetchPrimaryProfileSessions(params, async (_profile, path) => {
     const p = new URL(path, 'http://test').searchParams
     const o = Number(p.get('offset')), n = Number(p.get('limit'))
     return {sessions: [...[archived].slice(o, o+n), ...pins], total: 1, profile_totals: {default: 1}}
   }, {prefixForMerge: true})
   const merged = [...base.sessions].sort((a: any,b: any) => b.last_active-a.last_active)
   return routing.composeUnifiedSessionResponse(base, merged, offset, limit, base.total, base.profile_totals, [])
 })
 const result = await listEveryArchivedSession()
 expect(result.filter(row => row.archived).map(row => row.id)).toEqual(['old-archive'])
})
"""

CASES = [
    "cancelled_lookup_does_not_bind_old_connection",
    "restored_key_uses_its_owner_when_foreground_disconnected",
    "name_binding_is_scoped_after_a_completed_source_switch",
    "archive_loader_reaches_archive_behind_200_live_pins",
]


@pytest.fixture(scope="module")
def ui_results():
    (HERE / "vitest.review.mts").write_text(CONFIG, encoding="utf-8")
    (HERE / "test_zz_probe_ui.test.tsx").write_text(UI, encoding="utf-8")
    output = HERE / "ui-results.json"
    env = dict(os.environ, NODE_ENV="development", LEFTHOOK="0")
    run = subprocess.run(
        [shutil.which("node"), str(ROOT / "node_modules/vitest/vitest.mjs"), "run",
         "--config", str(HERE / "vitest.review.mts"), "--maxWorkers=1",
         "--reporter=json", "--outputFile", str(output)],
        cwd=ROOT / "apps/desktop", env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    (HERE / "ui-run.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    assert output.exists(), run.stdout + run.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    cases = [case for suite in payload.get("testResults", []) for case in suite.get("assertionResults", [])]
    assert len(cases) == len(CASES), run.stdout + run.stderr + str(payload)
    return {case["title"]: case for case in cases}


@pytest.mark.parametrize("name", CASES)
def test_desktop_contract(ui_results, name):
    case = ui_results[name]
    assert case["status"] == "passed", "\n".join(case.get("failureMessages", []))
