import { SerializeAddon } from '@xterm/addon-serialize'
import { Terminal } from '@xterm/xterm'
import { afterEach, expect, it, vi } from 'vitest'

import { createReviveHistory } from './revive-history'
import { createSnapshotPersister } from './snapshot-persister'
import { $terminals } from './terminals'

afterEach(() => vi.useRealTimers())

// Real xterm, SerializeAddon, revive history, persister and store; one Electron launch per pass:
// replay the saved buffer, print about 20 rows, persist.
it('keeps the saved revive buffer within the 200-row budget across relaunches, so old output ages out', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  $terminals.set([{ id: 'r', title: 'T', auto: true, cwd: '/w', kind: 'user' }])
  let revive = ''

  for (let launch = 0; launch < 16; launch += 1) {
    vi.setSystemTime((launch + 1) * 1000)
    const term = new Terminal({ allowProposedApi: true, cols: 80, rows: 24, scrollback: 1000, convertEol: true })
    const serialize = new SerializeAddon()
    term.loadAddon(serialize)
    const history = createReviveHistory(term, revive, () => false)
    history.restore()
    await history.ready

    const rows = Array.from({ length: 20 }, (_, row) => `s${launch} ${row}`)
    const body = launch === 0 ? 'API_TOKEN=sk-live-SECRET\r\n' : `${rows.join('\r\n')}\r\n`
    await new Promise<void>(resolve => term.write(`${body}$ `, resolve))

    createSnapshotPersister('r', {
      getLiveStartMarker: history.liveStartMarker, getShellName: () => 'bash', hasSessionActivity: () => true,
      isDisposed: () => false, isPersistent: () => false, probeCwd: () => {}, reviveBuffer: revive, serialize, term
    }).schedule()
    revive = $terminals.get()[0]!.reviveBuffer ?? ''
    term.dispose()
  }

  expect(revive.split('\r\n').length).toBeLessThanOrEqual(224)
  expect(revive).toContain('s15 19')
  expect(revive).not.toContain('sk-live-SECRET')
})
