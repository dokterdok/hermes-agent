import { SerializeAddon } from '@xterm/addon-serialize'
import { Terminal } from '@xterm/xterm'
import { afterEach, expect, it, vi } from 'vitest'

import { createReviveHistory } from './revive-history'
import { createSnapshotPersister } from './snapshot-persister'
import { $terminals } from './terminals'

afterEach(() => vi.useRealTimers())

// Real xterm, SerializeAddon, revive history, persister and store. The shell boots to `$ `
// before any input; `clear` then erases the display, which disposes the live-start marker, so
// every later save falls back to the whole normal buffer.
async function savedAfterClear(output: string): Promise<string> {
  vi.useFakeTimers({ toFake: ['Date'] })
  const restored = ['$ echo before', 'before'].join('\r\n')
  $terminals.set([{ id: 'tab', title: 'Terminal', auto: true, cwd: '/work', kind: 'user', reviveBuffer: restored }])
  const term = new Terminal({ allowProposedApi: true, cols: 80, rows: 24, scrollback: 1000, convertEol: true })
  const serialize = new SerializeAddon()
  term.loadAddon(serialize)
  const history = createReviveHistory(term, restored, () => false)
  history.restore()
  await history.ready
  let typed = false

  const persister = createSnapshotPersister('tab', {
    getLiveStartMarker: history.liveStartMarker, getShellName: () => 'zsh', hasSessionActivity: () => typed,
    isDisposed: () => false, isPersistent: () => false, probeCwd: () => {}, reviveBuffer: restored, serialize, term
  })

  for (const [at, chunk] of ['$ ', 'clear\r\n\x1b[H\x1b[2J\x1b[3J$ ', output].entries()) {
    await new Promise<void>(resolve => term.write(chunk, resolve))
    vi.setSystemTime((at + 1) * 1000)
    persister.schedule()
    typed = true
  }

  term.dispose()

  return $terminals.get()[0]!.reviveBuffer ?? ''
}

it('drops the idle prompt a cleared screen saves only where it repeats the boot prompt', async () => {
  // Kept, it replays above the fresh prompt on the next launch: a double prompt.
  expect(await savedAfterClear('seq 1 3\r\n1\r\n2\r\n3\r\n$ ')).toMatch(/seq 1 3\r\n1\r\n2\r\n3$/)
  // A short result after a blank line, or a command still running, is output: never trimmed.
  expect(await savedAfterClear("printf '\\nRESULT=42\\n'\r\n\r\nRESULT=42\r\n$ ")).toMatch(/\r\n\r\nRESULT=42$/)
  expect(await savedAfterClear("printf 'first\\nlast'; sleep 30\r\nfirst\r\nlast")).toMatch(/\r\nfirst\r\nlast$/)
})
