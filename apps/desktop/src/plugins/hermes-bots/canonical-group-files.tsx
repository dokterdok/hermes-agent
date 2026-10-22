/** Files for a gateway room: its shared versions, newest first, searchable, in exact same-name rows. */
import {
  Button,
  Codicon,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  Loader,
  SearchField,
  Tip
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  type CanonicalFile,
  CanonicalFilesError,
  type CanonicalFilesPage,
  FILES_MAX_QUERY,
  type FilesFailure,
  filesFailure,
  filesFollow,
  listCanonicalFiles,
  offersFilesCatalog
} from './canonical-files-client'
import type { CanonicalGroupEvent } from './canonical-group-history'
import { useCanonicalGroupLabels } from './canonical-group-labels'
import { type CanonicalGroupBinding, canonicalGroupRequest } from './canonical-groups'

type Labels = ReturnType<typeof useCanonicalGroupLabels>

interface Listing {
  pages: CanonicalFilesPage[]
  index: number
  query: string
  loading: boolean
  failure: FilesFailure | null
}

/** The newest room-log message that shared a file: the list offers Show latest when it is newer. */
export function latestFileSeq(events: CanonicalGroupEvent[]) {
  return events.reduce((latest, event) => event.payload.attachments?.length ? Math.max(latest, event.seq) : latest, 0)
}

/** Whether the room's gateway lists the Files catalog. Read once per visible room view; a failed
 *  read never offers Files and is tried again every two seconds while the room is visible. */
function useFilesOffered(binding: CanonicalGroupBinding, visible: boolean) {
  const [offered, setOffered] = useState<boolean | undefined>(undefined)
  const [attempt, setAttempt] = useState(0)
  const answered = offered !== undefined

  useEffect(() => {
    if (answered || !visible) {return}
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    void canonicalGroupRequest<unknown>(binding, 'groups.capabilities').then(
      value => { if (!cancelled) {setOffered(offersFilesCatalog(value))} },
      () => { if (!cancelled) {timer = setTimeout(() => setAttempt(value => value + 1), 2000)} }
    )

    return () => { cancelled = true; clearTimeout(timer) }
  }, [binding, visible, answered, attempt])

  return offered === true
}

/** One dialog lifetime. A new read retires the one before it; closing retires them all. */
function useCanonicalFiles(binding: CanonicalGroupBinding) {
  const [listing, setListing] = useState<Listing>({ pages: [], index: 0, query: '', loading: true, failure: null })
  // Synchronous ownership of the current read, not a mirror of an external store.
  const model = useRef({ listing, generation: 0, controller: new AbortController(), again: () => {} })

  const publish = useCallback((patch: Partial<Listing>) => {
    model.current.listing = { ...model.current.listing, ...patch }
    setListing(model.current.listing)
  }, [])

  const load = useCallback(async (mode: 'latest' | 'older') => {
    const current = model.current
    const before = current.listing
    const held = before.pages[before.index]
    current.controller.abort()
    const controller = current.controller = new AbortController()
    const generation = ++current.generation
    current.again = () => void load(mode)
    publish({ loading: true, failure: null, ...(mode === 'latest' ? { pages: [], index: 0 } : {}) })

    try {
      const page = await listCanonicalFiles(binding, {
        cursor: mode === 'older' ? held?.nextCursor ?? undefined : undefined,
        query: before.query.trim() || undefined
      }, controller.signal)

      if (generation !== current.generation) {return}

      if (mode === 'older' && held) {
        const last = before.pages.slice(0, before.index + 1).flatMap(earlier => earlier.items).at(-1)

        // A continuation must extend the same snapshot and authority, never repeat or loop.
        if (page.snapshotSeq !== held.snapshotSeq || page.authority !== held.authority ||
          (page.nextCursor !== null && before.pages.some(earlier => earlier.nextCursor === page.nextCursor)) ||
          (last && page.items.length > 0 && !filesFollow(last, page.items[0]))) {
          throw new CanonicalFilesError('cursor')
        }

        publish({ loading: false, pages: [...before.pages.slice(0, before.index + 1), page], index: before.index + 1 })
      } else {
        publish({ loading: false, pages: [page], index: 0 })
      }
    } catch (error) {
      if (generation === current.generation && !controller.signal.aborted) {
        publish({ loading: false, failure: filesFailure(error) })
      }
    }
  }, [binding, publish])

  useEffect(() => {
    const current = model.current
    const timer = setTimeout(() => void load('latest'), listing.query.trim() ? 250 : 0)

    return () => {
      clearTimeout(timer)
      current.generation++
      current.controller.abort()
    }
  }, [listing.query, load])

  const move = (step: number) => {
    const { listing: current } = model.current
    const index = current.index + step

    if (current.loading || index < 0) {return}

    if (current.pages[index]) {publish({ index, failure: null })}
    else if (step > 0 && current.pages[current.index]?.nextCursor) {void load('older')}
  }

  return {
    ...listing,
    page: listing.pages[listing.index] as CanonicalFilesPage | undefined,
    setQuery: (value: string) => {
      const query = [...value].slice(0, FILES_MAX_QUERY).join('')

      if (query !== model.current.listing.query) {publish({ query, pages: [], index: 0, loading: true, failure: null })}
    },
    older: () => move(1),
    newer: () => move(-1),
    latest: () => void load('latest'),
    retry: () => model.current.again()
  }
}

function fileSize(bytes: number, locale?: string) {
  const unit = bytes < 1000 ? 'byte' : bytes < 1_000_000 ? 'kilobyte' : 'megabyte'
  const value = unit === 'byte' ? bytes : bytes / (unit === 'kilobyte' ? 1000 : 1_000_000)

  return new Intl.NumberFormat(locale, { style: 'unit', unit, unitDisplay: 'short',
    maximumFractionDigits: value < 10 ? 1 : 0 }).format(value)
}

/** Name, sharer, time to the second, type and size: what tells two same-name versions apart. */
function fileDetails(file: CanonicalFile, labels: Labels) {
  const date = new Date(file.sharedAt * 1000)
  const sameDay = date.toDateString() === new Date().toDateString()

  // Seconds on every page: two versions can share a name, size and minute.
  const time = new Intl.DateTimeFormat(labels.locale, {
    ...(sameDay ? {} : { year: 'numeric', month: 'short', day: 'numeric' } as const),
    hour: 'numeric', minute: '2-digit', second: '2-digit'
  }).format(date)

  // The gateway labels the room owner's own shares `You`.
  const sharer = file.sharer.kind === 'user' && file.sharer.label === 'You' ? labels.filesYou : file.sharer.label
  const type = (file.name.includes('.') ? file.name.split('.').pop() : file.mime.split('/')[1])?.toUpperCase() ?? ''

  return { sharer, details: ` · ${time} · ${type} · ${fileSize(file.size, labels.locale)}` }
}

function FileRow({ file, labels, active, onFocus }: {
  file: CanonicalFile; labels: Labels; active: boolean; onFocus: () => void
}) {
  const { sharer, details } = fileDetails(file, labels)

  return <div aria-label={`${file.name} · ${sharer}${details}`} className="min-w-0 py-1.5 outline-none focus-visible:ring-1"
    data-file-row onFocus={onFocus} role="listitem" tabIndex={active ? 0 : -1}>
    <div className="flex min-w-0 items-center gap-2">
      <Codicon className="shrink-0" name={file.kind === 'pdf' ? 'file-pdf' : file.kind === 'image' ? 'file-media' : 'file'} />
      <div className="min-w-0 flex-1">
        <bdi className="block truncate text-xs font-medium" title={file.name}>{file.name}</bdi>
        <div className="truncate text-[0.65rem]"><bdi>{sharer}</bdi>{details}</div>
      </div>
    </div>
  </div>
}

function FileRows({ items, labels, loading }: { items: CanonicalFile[]; labels: Labels; loading: boolean }) {
  const [active, setActive] = useState(0)

  return <div aria-busy={loading} className="min-h-0 flex-1 overflow-y-auto" onKeyDown={event => {
    const rows = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[data-file-row]'))
    const row = (event.target as HTMLElement).closest<HTMLElement>('[data-file-row]')

    if (row && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault()
      rows[Math.max(0, Math.min(rows.length - 1, rows.indexOf(row) + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus()
    }
  }} role="list">
    {items.map((file, index) => <FileRow active={active === index} file={file}
      key={`${file.eventId}:${file.attachmentId}`} labels={labels} onFocus={() => setActive(index)} />)}
  </div>
}

function filesProblem(failure: FilesFailure | null, labels: Labels) {
  return failure === 'cursor' ? labels.filesExpired : failure === 'access' ? labels.filesAccess
    : failure === 'unavailable' || failure === 'timeout' ? labels.filesUnavailable : labels.filesError
}

type FilesListing = ReturnType<typeof useCanonicalFiles>

/** A refused cursor restarts from the newest files; an access refusal has nothing to retry. */
function FilesRecovery({ files, labels }: { files: FilesListing; labels: Labels }) {
  if (files.failure === 'cursor') {
    return <Button onClick={files.latest} size="inline" type="button" variant="textStrong">{labels.showLatest}</Button>
  }

  return files.failure === 'access' ? null
    : <Button onClick={files.retry} size="inline" type="button" variant="textStrong">{labels.retry}</Button>
}

function FilesBody({ files, labels }: { files: FilesListing; labels: Labels }) {
  const { page, failure } = files

  if (page?.items.length) {
    return <FileRows items={page.items} key={`${files.index}:${page.snapshotSeq}`} labels={labels} loading={files.loading} />
  }

  if (files.loading) {return <Loader className="m-auto size-16" label={labels.filesLoading} type="lemniscate-bloom" />}

  if (failure) {
    return <ErrorState className="my-auto" title={<p className="text-sm font-medium">{filesProblem(failure, labels)}</p>}>
      <FilesRecovery files={files} labels={labels} />
    </ErrorState>
  }

  const empty = page?.nextCursor || files.index > 0 ? labels.filesPageEmpty : files.query ? labels.filesNoResults : labels.filesEmpty

  return <div className="my-auto">
    <EmptyState title={empty} />
    {files.query && <div className="flex justify-center">
      <Button onClick={() => files.setQuery('')} size="inline" type="button" variant="textStrong">{labels.filesClearSearch}</Button>
    </div>}
  </div>
}

function FilesFooter({ files, labels, latest }: { files: FilesListing; labels: Labels; latest: number }) {
  const { page, failure } = files
  const newer = files.pages[0] !== undefined && latest > files.pages[0].snapshotSeq

  return <div className="flex min-h-8 flex-wrap items-center justify-end gap-2">
    {page?.items.length && failure ? <div className="mr-auto flex flex-wrap items-center gap-2 text-xs" role="status">
      <span>{filesProblem(failure, labels)}</span><FilesRecovery files={files} labels={labels} />
    </div> : null}
    {newer && <Button disabled={files.loading} onClick={files.latest} size="inline" type="button" variant="textStrong">
      {labels.showLatest}</Button>}
    <Tip label={labels.refreshFiles}>
      <Button aria-label={labels.refreshFiles} disabled={files.loading} onClick={files.latest} size="icon-xs"
        type="button" variant="ghost"><Codicon name="refresh" /></Button>
    </Tip>
    <Tip label={labels.newerFiles}>
      <Button aria-label={labels.newerFiles} disabled={files.index === 0 || files.loading} onClick={files.newer}
        size="icon-xs" type="button" variant="ghost"><Codicon name="chevron-left" /></Button>
    </Tip>
    <Tip label={labels.olderFiles}>
      <Button aria-label={labels.olderFiles} disabled={!page?.nextCursor || files.loading} onClick={files.older}
        size="icon-xs" type="button" variant="ghost"><Codicon name="chevron-right" /></Button>
    </Tip>
  </div>
}

function FilesDialog({ binding, roomName, latest, onClose }: {
  binding: CanonicalGroupBinding; roomName: string; latest: number; onClose: () => void
}) {
  const labels = useCanonicalGroupLabels()
  const files = useCanonicalFiles(binding)
  const search = useRef<HTMLInputElement>(null)

  return <Dialog onOpenChange={value => { if (!value) {onClose()} }} open>
    <DialogContent bodyClassName="flex min-h-0 flex-1 flex-col gap-3" className="h-[min(36rem,85vh)] max-w-xl"
      onKeyDown={event => {
        const target = event.target as HTMLElement

        if (event.key === '/' && !target.matches('input,textarea,[contenteditable="true"]') &&
          !event.ctrlKey && !event.metaKey && !event.altKey) {
          event.preventDefault()
          search.current?.focus()
        } else if (event.key === 'ArrowDown' && target === search.current) {
          const row = event.currentTarget.querySelector<HTMLElement>('[data-file-row]')

          if (row) {
            event.preventDefault()
            row.focus()
          }
        }
      }}
      onOpenAutoFocus={event => { event.preventDefault(); search.current?.focus() }}>
      <DialogHeader>
        <DialogTitle>{labels.files}</DialogTitle>
        <DialogDescription><bdi className="block truncate" title={roomName}>{roomName}</bdi></DialogDescription>
      </DialogHeader>
      <SearchField aria-label={labels.searchFiles} containerClassName="w-full" inputClassName="w-full" inputRef={search}
        loading={files.loading && Boolean(files.page)} onChange={files.setQuery} placeholder={labels.searchFiles} value={files.query} />
      <FilesBody files={files} labels={labels} />
      <FilesFooter files={files} labels={labels} latest={latest} />
    </DialogContent>
  </Dialog>
}

function FilesButton({ binding, roomName, latest }: { binding: CanonicalGroupBinding; roomName: string; latest: number }) {
  const labels = useCanonicalGroupLabels()
  const [open, setOpen] = useState(false)

  return <>
    <Button onClick={() => setOpen(true)} type="button">{labels.files}</Button>
    {open && <FilesDialog binding={binding} latest={latest} onClose={() => setOpen(false)} roomName={roomName} />}
  </>
}

/** The room header's Files control: offered only when the gateway lists `groups.attachment.list`, and
 *  only while the room is visible (hiding it closes the dialog and retires its reads). */
export function CanonicalGroupFiles({ binding, roomName, latest, visible }: {
  binding: CanonicalGroupBinding; roomName?: string; latest: number; visible: boolean
}) {
  const offered = useFilesOffered(binding, visible)

  return offered && visible
    ? <FilesButton binding={binding} latest={latest} roomName={roomName || binding.roomName || ''} /> : null
}
