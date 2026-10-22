import type { SerializeAddon } from '@xterm/addon-serialize'
import type { IMarker, Terminal } from '@xterm/xterm'

import {
  mergeReviveSnapshot,
  PERSISTENT_SESSION_SCROLLBACK,
  resolveLiveSnapshotWindow,
  trimRepeatedPrompt
} from './revive-snapshot'
import { updateTerminalReviveBuffer } from './terminals'

// Leading-edge throttle window for capturing history. The first output after an
// idle gap persists almost immediately (so `cmd; quit` is on disk before the
// renderer tears down), then at most once per window while output streams.
const SNAPSHOT_THROTTLE_MS = 750

// The last `rows` lines of the restored prefix, byte for byte: the saved buffer holds at most
// PERSISTENT_SESSION_SCROLLBACK rows of restored plus live output, so restored history ages out
// as each session adds rows. Cut after the line break `rows` from the end; never split and rejoin
// (that rewrites bare \n) and never clean restored text.
function lastRestoredRows(restored: string, rows: number): string {
  if (rows <= 0) {
    return ''
  }

  let cut = restored.length

  for (let row = 0; row < rows; row += 1) {
    cut = restored.lastIndexOf('\n', cut - 1)

    if (cut < 0) {
      return restored
    }
  }

  return restored.slice(cut + 1)
}

interface SnapshotPersisterOptions {
  /** Boundary between replayed history and this session's PTY output. */
  getLiveStartMarker: () => IMarker | undefined
  getShellName: () => string
  hasSessionActivity: () => boolean
  isDisposed: () => boolean
  /** Server-owned shells replay their own history; nothing to persist here. */
  isPersistent: () => boolean
  /** Refresh the persisted cwd after each capture (throttled by the tracker). */
  probeCwd: () => void
  /** The revive buffer replayed at mount, kept ahead of live output byte-for-byte. */
  reviveBuffer: string
  serialize: SerializeAddon
  term: Terminal
}

// Capture the buffer on a leading-edge throttle and persist synchronously via
// the store. No unload hook: by the time the user quits, a recent snapshot is
// already on disk (the prior beforeunload-based attempt lost the last output).
export function createSnapshotPersister(
  id: string,
  {
    getLiveStartMarker,
    getShellName,
    hasSessionActivity,
    isDisposed,
    isPersistent,
    probeCwd,
    reviveBuffer,
    serialize,
    term
  }: SnapshotPersisterOptions
): { cancel: () => void; schedule: () => void } {
  let snapshotTimer = 0
  let lastSnapshotAt = 0
  // The fresh shell's boot output, read before any input: its last rows are the shell's own
  // prompt, the evidence that lets a trailing prompt be trimmed instead of guessed at.
  let bootRows: string[] = []

  const readBootRows = () => {
    try {
      const marker = getLiveStartMarker()
      const normal = term.buffer.normal
      const cursorLine = normal.baseY + normal.cursorY

      if (term.buffer.active.type === 'normal' && marker && term.markers.includes(marker) && marker.line >= 0 &&
        marker.line <= cursorLine) {
        bootRows = serialize.serialize({ excludeAltBuffer: true, range: { end: cursorLine, start: marker.line } }).split(/\r?\n/)
      }
    } catch {
      // Keep the last good read.
    }
  }

  const persistSnapshot = () => {
    if (isDisposed() || isPersistent()) {
      return
    }

    lastSnapshotAt = Date.now()

    // No user input this session: never re-serialize. The live buffer now holds
    // replayed history plus fresh boot output, and re-saving that is exactly
    // what grew idle tabs by one prompt per relaunch (#61572). Preserve the
    // prior snapshot byte-for-byte; legacy text is ambiguous and must not be
    // auto-deleted merely because it resembles a prompt.
    if (!hasSessionActivity()) {
      readBootRows()

      return
    }

    try {
      const liveStartMarker = getLiveStartMarker()

      if (term.buffer.active.type !== 'normal' || !liveStartMarker) {
        return
      }

      const normal = term.buffer.normal
      const cursorLine = normal.baseY + normal.cursorY
      let lastContentLine = normal.length - 1

      while (lastContentLine > cursorLine && !normal.getLine(lastContentLine)?.translateToString(true)) {
        lastContentLine -= 1
      }

      // `normal.length` includes blank viewport rows below the cursor. A range
      // budget based on that capacity can start after the cursor and serialize
      // nothing (for example after a tall resize). The cursor is the live end;
      // if real content exists below it, provenance is uncertain and falls back.
      const liveWindow = resolveLiveSnapshotWindow(
        liveStartMarker.line,
        cursorLine,
        term.markers.includes(liveStartMarker) && lastContentLine <= cursorLine
      )

      // Once live output alone exceeds the replay budget, the restored prefix
      // has scrolled out and should no longer be carried into future sessions.
      // Without a window, a reset, clear-screen, or scrollback trim invalidated
      // the logical boundary: the current normal buffer is authoritative, but
      // its restored/live provenance is unknown, so persist it without
      // text-based greeting or prompt cleanup rather than risk deleting real
      // output. Only a trailing prompt that repeats the shell's own boot prompt
      // goes, or the next launch replays it above the fresh one.
      const nextSnapshot = liveWindow
        ? mergeReviveSnapshot(
            liveWindow.keepRestored
              ? lastRestoredRows(reviveBuffer, PERSISTENT_SESSION_SCROLLBACK - (cursorLine - liveWindow.start + 1))
              : '',
            serialize.serialize({ excludeAltBuffer: true, range: { end: cursorLine, start: liveWindow.start } }),
            getShellName(),
            liveWindow.keepRestored,
            bootRows
          )
        : trimRepeatedPrompt(serialize.serialize({ excludeAltBuffer: true, scrollback: PERSISTENT_SESSION_SCROLLBACK }), bootRows)

      updateTerminalReviveBuffer(id, nextSnapshot)
    } catch {
      // Best-effort restore: never let serialization break a live terminal.
    }

    // A user command may have `cd`'d; refresh the persisted cwd (throttled).
    probeCwd()
  }

  const scheduleSnapshot = () => {
    if (snapshotTimer) {
      return
    }

    const elapsed = Date.now() - lastSnapshotAt

    if (elapsed >= SNAPSHOT_THROTTLE_MS) {
      persistSnapshot()

      return
    }

    snapshotTimer = window.setTimeout(() => {
      snapshotTimer = 0
      persistSnapshot()
    }, SNAPSHOT_THROTTLE_MS - elapsed)
  }

  return {
    cancel: () => {
      if (snapshotTimer) {
        window.clearTimeout(snapshotTimer)
      }
    },
    schedule: scheduleSnapshot
  }
}
