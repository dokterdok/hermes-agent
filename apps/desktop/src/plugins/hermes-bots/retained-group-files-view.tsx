/** Local-only counterpart to the existing canonical Files dialog. */
import {
  Button,
  Codicon,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  SearchField,
  Tip
} from '@hermes/plugin-sdk'
import { useLayoutEffect, useRef, useState } from 'react'

import { useCanonicalFilesLabels } from './canonical-files-labels'
import { GROUP_FILES_MAX_QUERY_LENGTH } from './group-files-parser'
import {
  createRetainedFilesLoader,
  type RetainedFileItem,
  type RetainedRoomBinding,
  saveRetainedFile
} from './retained-group-files'
import { useRetainedGroupLabels } from './retained-group-labels'

function dateLabel(at: number, locale: string) {
  return Number.isFinite(at) ? new Date(at).toLocaleString(locale) : ''
}

export function RetainedFileRow({ item, signal }: { item: RetainedFileItem; signal: AbortSignal }) {
  const b = useCanonicalFilesLabels()
  const retained = useRetainedGroupLabels()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const pending = useRef(false)
  const available = item.available && item.current() && !signal.aborted

  const download = async () => {
    if (!available || pending.current) {
      return
    }

    pending.current = true
    setBusy(true)
    setError(false)

    try {
      await saveRetainedFile(item, signal)
    } catch {
      if (!signal.aborted) {
        setError(true)
      }
    } finally {
      pending.current = false

      if (!signal.aborted) {
        setBusy(false)
      }
    }
  }

  return (
    <div className="min-w-0 py-2" role="listitem">
      <div className="flex min-w-0 items-center gap-2">
        <Codicon
          name={item.attachment.kind === 'image' ? 'file-media' : item.attachment.kind === 'pdf' ? 'file-pdf' : 'file'}
        />
        <div className="min-w-0 flex-1">
          <bdi className="block truncate text-xs font-medium" title={item.attachment.name}>
            {item.attachment.name}
          </bdi>
          <p className="truncate text-[0.65rem] text-(--ui-text-tertiary)">
            <bdi>{item.speaker}</bdi>
            {` · ${dateLabel(item.at, b.locale)}`}
            {item.size !== null && ` · ${new Intl.NumberFormat(b.locale).format(item.size)} B`}
          </p>
        </div>
        <Tip label={`${b.download}: ${item.attachment.name}`}>
          <Button
            aria-busy={busy}
            aria-label={`${b.download}: ${item.attachment.name}`}
            disabled={!available || busy}
            onClick={() => void download()}
            size="icon-xs"
            type="button"
            variant="ghost"
          >
            <Codicon name={busy ? 'loading' : 'cloud-download'} spinning={busy} />
          </Button>
        </Tip>
      </div>
      {!item.available && <p className="text-xs text-(--ui-text-tertiary)">{retained.noBytes}</p>}
      {item.available && !item.current() && <p className="text-xs text-(--ui-text-tertiary)">{b.fileGone}</p>}
      {error && (
        <p className="text-xs text-(--ui-text-secondary)" role="alert">
          {b.fileVerificationFailed}
        </p>
      )}
    </div>
  )
}

function RetainedFilesDialog({ binding, onClose }: { binding: RetainedRoomBinding; onClose: () => void }) {
  const b = useCanonicalFilesLabels()
  const [load] = useState(() => createRetainedFilesLoader(binding))
  const [state, setState] = useState(() => ({ query: '', pages: [load()], index: 0 }))
  const [signal, setSignal] = useState(() => new AbortController())
  const activeSignal = useRef(signal)
  const input = useRef<HTMLInputElement>(null)
  useLayoutEffect(
    () => () => {
      activeSignal.current.abort()
      load.clear()
    },
    [load]
  )

  const retire = () => {
    activeSignal.current.abort()
    const next = new AbortController()
    activeSignal.current = next
    setSignal(next)
  }

  const latest = (query = state.query) => {
    retire()
    setState({ query, pages: [load({ query })], index: 0 })
  }

  const older = () => {
    const current = state.pages[state.index]

    if (!current.nextCursor) {
      return
    }

    retire()

    const pages = state.pages[state.index + 1]
      ? state.pages
      : [...state.pages, load({ query: state.query, cursor: current.nextCursor })]

    setState({ ...state, pages, index: state.index + 1 })
  }

  const page = state.pages[state.index]

  return (
    <Dialog
      onOpenChange={open => {
        if (!open) {
          activeSignal.current.abort()
          onClose()
        }
      }}
      open
    >
      <DialogContent
        bodyClassName="flex min-h-0 flex-1 flex-col gap-3"
        className="h-[min(36rem,85vh)] max-w-xl"
        onOpenAutoFocus={event => {
          event.preventDefault()
          input.current?.focus()
        }}
      >
        <DialogHeader>
          <DialogTitle>{b.sharedFiles}</DialogTitle>
          <DialogDescription>
            <bdi className="block truncate">{binding.group}</bdi>
          </DialogDescription>
        </DialogHeader>
        <SearchField
          aria-label={b.searchSharedFiles}
          inputRef={input}
          onChange={query => latest([...query].slice(0, GROUP_FILES_MAX_QUERY_LENGTH).join(''))}
          placeholder={b.searchSharedFiles}
          value={state.query}
        />
        <div className="min-h-0 flex-1 overflow-y-auto" role="list">
          {page.items.map(item => (
            <RetainedFileRow item={item} key={item.key} signal={signal.signal} />
          ))}
          {!page.items.length && (
            <p className="py-6 text-sm text-(--ui-text-tertiary)" role="status">
              {state.query ? b.sharedFilesNoResults : b.sharedFilesEmpty}
            </p>
          )}
        </div>
        <footer className="flex min-h-8 items-center justify-end gap-2">
          <Tip label={b.filesRefresh}>
            <Button aria-label={b.filesRefresh} onClick={() => latest()} size="icon-xs" type="button" variant="ghost">
              <Codicon name="refresh" />
            </Button>
          </Tip>
          <Tip label={b.newerFiles}>
            <Button
              aria-label={b.newerFiles}
              disabled={state.index === 0}
              onClick={() => {
                retire()
                setState({ ...state, index: state.index - 1 })
              }}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Codicon name="chevron-left" />
            </Button>
          </Tip>
          <Tip label={b.olderFiles}>
            <Button
              aria-label={b.olderFiles}
              disabled={!page.nextCursor}
              onClick={older}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Codicon name="chevron-right" />
            </Button>
          </Tip>
        </footer>
      </DialogContent>
    </Dialog>
  )
}

export function RetainedFilesControl({ binding }: { binding: RetainedRoomBinding }) {
  const b = useCanonicalFilesLabels()
  const [open, setOpen] = useState(false)

  return (
    <>
      <Tip label={b.sharedFiles}>
        <Button aria-label={b.sharedFiles} onClick={() => setOpen(true)} size="icon-sm" type="button" variant="ghost">
          <Codicon name="files" />
        </Button>
      </Tip>
      {open && <RetainedFilesDialog binding={binding} onClose={() => setOpen(false)} />}
    </>
  )
}
