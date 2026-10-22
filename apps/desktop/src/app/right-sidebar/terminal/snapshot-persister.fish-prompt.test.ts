import { SerializeAddon } from '@xterm/addon-serialize'
import { Terminal } from '@xterm/xterm'
import { afterEach, expect, it, vi } from 'vitest'

import { createReviveHistory } from './revive-history'
import { createSnapshotPersister } from './snapshot-persister'
import { $terminals } from './terminals'

afterEach(() => vi.useRealTimers())

const GREETING = 'Welcome to fish, the friendly interactive shell\r\nType help for instructions on how to use fish\r\n'

// Real xterm, SerializeAddon, revive history, persister and store: a fresh Fish boots to its
// greeting and prompt before any input, then the user runs one command.
async function savedFishSession(afterInput: string): Promise<string> {
  vi.useFakeTimers({ toFake: ['Date'] })
  $terminals.set([{ id: 'fish', title: 'Terminal', auto: true, cwd: '/repo', kind: 'user' }])
  const term = new Terminal({ allowProposedApi: true, cols: 80, rows: 24, scrollback: 1000, convertEol: true })
  const serialize = new SerializeAddon()
  term.loadAddon(serialize)
  const history = createReviveHistory(term, '', () => false)
  history.restore()
  await history.ready
  let typed = false

  const persister = createSnapshotPersister('fish', {
    getLiveStartMarker: history.liveStartMarker, getShellName: () => '/usr/bin/fish', hasSessionActivity: () => typed,
    isDisposed: () => false, isPersistent: () => false, probeCwd: () => {}, reviveBuffer: '', serialize, term
  })

  for (const [at, chunk] of [`${GREETING}~/repo> `, afterInput].entries()) {
    await new Promise<void>(resolve => term.write(chunk, resolve))
    vi.setSystemTime((at + 1) * 1000)
    persister.schedule()
    typed = true
  }

  term.dispose()

  return $terminals.get()[0]!.reviveBuffer ?? ''
}

it('drops the Fish idle prompt that repeats its boot prompt, so a relaunch shows one prompt', async () => {
  expect(await savedFishSession('echo hi\r\nhi\r\n~/repo> ')).toBe('~/repo> echo hi\r\nhi')
  // A command still running keeps its last line.
  expect(await savedFishSession("printf 'a\\nlast'; sleep 9\r\na\r\nlast")).toMatch(/\r\na\r\nlast$/)
})
