import { Button, Codicon, Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
  gatewayActivationEpoch, host, Tip, useI18n, useValue } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'

import { canonicalFilesFailure } from './canonical-files-client'
import { useCanonicalGroupLabels } from './canonical-group-labels'
import type { CanonicalGroupRoute } from './canonical-groups'
import { readSavedGroupPreview, readSavedGroups, savedGroupHolder } from './canonical-saved-group-client'
import type { SavedGroupCopy, SavedGroupPage, SavedGroupPreview } from './canonical-saved-group-client'

type Status = 'loading' | 'ready' | 'unsupported' | 'denied' | 'failed'

export function CanonicalSavedGroups() {
  const connectionId = useValue(host.state.connectionId)
  const profile = useValue(host.state.profile)
  const gateway = useValue(host.state.gateway)
  const epoch = gatewayActivationEpoch()

  if (!connectionId) {return null}

  return <SavedGroupsBrowser connected={gateway === 'open'} epoch={epoch}
    key={JSON.stringify([connectionId, profile, epoch])} route={{ connectionId, profile }} />
}

function SavedGroupsBrowser({ route: initialRoute, epoch, connected }: {
  route: CanonicalGroupRoute; epoch: number; connected: boolean
}) {
  const [route] = useState(() => ({ ...initialRoute }))
  const labels = useCanonicalGroupLabels()
  const { locale } = useI18n()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<Status>('loading')
  const [page, setPage] = useState<SavedGroupPage | null>(null)
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const [holder, setHolder] = useState<string | null>(null)
  const [selected, setSelected] = useState<SavedGroupCopy | null>(null)
  const [preview, setPreview] = useState<SavedGroupPreview | null>(null)
  const [previewStatus, setPreviewStatus] = useState<Status>('ready')
  const operation = useRef({ version: 0, controller: new AbortController() })
  const alive = useRef(true)

  const sourceCurrent = () => alive.current && gatewayActivationEpoch() === epoch &&
    host.state.connectionId.get() === route.connectionId && host.state.profile.get() === route.profile &&
    host.state.gateway.get() === 'open'

  const invalidate = () => {
    operation.current.controller.abort()
    operation.current = { version: operation.current.version + 1, controller: new AbortController() }
  }

  const begin = () => {
    invalidate()
    const { version, controller } = operation.current

    return { signal: controller.signal, current: () => sourceCurrent() && operation.current.version === version }
  }

  const fail = (error: unknown): Status => {
    const failure = canonicalFilesFailure(error)

    return failure === 'access' || failure === 'scope' ? 'denied' : failure === 'unavailable' ? 'unsupported' : 'failed'
  }

  const load = async (nextCursors: Array<string | null> = [null]) => {
    if (!sourceCurrent()) {return}
    const intent = begin()
    setStatus('loading')
    setPage(null)
    setSelected(null)
    setPreview(null)

    try {
      const owner = await savedGroupHolder(route, intent.signal)

      if (!intent.current()) {return}

      if (owner === null) {
        setHolder(null)
        setStatus('unsupported')

        return
      }

      const next = await readSavedGroups(route, nextCursors.at(-1) ?? null, intent.signal)

      if (!intent.current()) {return}

      if (next.targetGatewayId !== owner) {throw new Error('Saved-copy holder changed')}

      if (next.nextRoomId !== null && nextCursors.includes(next.nextRoomId)) {throw new Error('Repeated saved-copy cursor')}
      setHolder(next.targetGatewayId)
      setPage(next)
      setCursors(nextCursors)
      setStatus('ready')
    } catch (error) {
      if (intent.current()) {setHolder(null); setPage(null); setStatus(fail(error))}
    }
  }

  const select = async (copy: SavedGroupCopy) => {
    if (!sourceCurrent() || !holder || status !== 'ready') {return}
    const intent = begin()
    setSelected(copy)
    setPreview(null)
    setPreviewStatus('loading')

    try {
      const result = await readSavedGroupPreview(route, copy, holder, intent.signal)

      if (intent.current()) {setPreview(result); setPreviewStatus('ready')}
    } catch (error) {
      if (!intent.current()) {return}
      const failure = fail(error)
      setPreview(null)
      setPreviewStatus(failure)

      if (failure === 'denied' || failure === 'unsupported') {
        setHolder(null); setPage(null); setSelected(null); setStatus(failure)
      }
    }
  }

  const close = () => {
    invalidate()
    setOpen(false)
    setSelected(null)
    setPreview(null)
  }

  // eslint-disable-next-line no-restricted-syntax -- WHY: mounted lifetime only; callbacks read source atoms directly, never mirrored refs.
  useEffect(() => {
    alive.current = true

    if (connected) {void load()}

    return () => { alive.current = false; invalidate() }
    // Route and activation identity are frozen by the keyed parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected])

  const date = (at: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(at)

  const failureLabel = status === 'denied' ? labels.savedCopiesDenied
    : status === 'unsupported' ? labels.savedCopiesUnavailable : labels.savedCopiesFailed

  return <>
    {(open || (status !== 'unsupported' && status !== 'denied')) && <Button disabled={!connected}
      onClick={() => { if (sourceCurrent()) {setOpen(true); void load()} }} type="button" variant="ghost">
      <Codicon name="history" />{labels.viewSavedCopies}
    </Button>}
    {open && <Dialog onOpenChange={value => { if (!value) {close()} }} open>
      <DialogContent bodyClassName="flex flex-col gap-4" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{labels.savedCopies}</DialogTitle>
          <DialogDescription>{labels.savedCopiesNotResumed} {labels.savedCopiesRecentWork}</DialogDescription>
        </DialogHeader>
        {!connected ? <p role="status">{labels.savedCopiesOffline}</p> : <>
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-(--ui-text-secondary)">{labels.savedCopy}</p>
            <Tip label={labels.refresh}><Button aria-label={labels.refresh} onClick={() => void load()}
              size="icon-sm" type="button" variant="ghost"><Codicon name="refresh" /></Button></Tip>
          </div>
          {status === 'loading' && <p role="status">{labels.savedCopiesLoading}</p>}
          {status !== 'ready' && status !== 'loading' && <p role="alert">{failureLabel}</p>}
          {status === 'ready' && page && <>
            {!page.copies.length && <p role="status">{labels.savedCopiesEmpty}</p>}
            <ul aria-label={labels.savedCopies} className="grid max-h-64 gap-1 overflow-y-auto">
              {page.copies.map(copy => <li key={copy.roomId}>
                <Button aria-pressed={selected?.roomId === copy.roomId} className="w-full justify-start whitespace-normal text-start"
                  onClick={() => void select(copy)} type="button" variant={selected?.roomId === copy.roomId ? 'secondary' : 'ghost'}>
                  <Codicon name="history" />
                  <span className="min-w-0 flex-1 wrap-anywhere">
                    <span className="block">{copy.name}</span>
                    <time className="block text-xs font-normal text-(--ui-text-secondary)" dateTime={new Date(copy.updatedAt).toISOString()}>{date(copy.updatedAt)}</time>
                  </span>
                  <span className="min-w-0 max-w-[40%] wrap-anywhere text-xs font-normal">{copy.status === 'retired' ? labels.savedCopyRetired : copy.status === 'needs_review'
                    ? labels.savedCopyNeedsReview : copy.ended ? labels.savedGroupEnded : copy.savedThrough < copy.advertisedLatest ? labels.savedCopyPartial : labels.savedCopy}</span>
                </Button>
              </li>)}
            </ul>
            <div className="flex justify-end gap-1">
              <Tip label={labels.savedCopiesPrevious}><Button aria-label={labels.savedCopiesPrevious} disabled={cursors.length < 2}
                onClick={() => void load(cursors.slice(0, -1))} size="icon-sm" type="button" variant="ghost"><Codicon name="chevron-left" /></Button></Tip>
              <Tip label={labels.savedCopiesNext}><Button aria-label={labels.savedCopiesNext} disabled={page.nextRoomId === null}
                onClick={() => void load([...cursors, page.nextRoomId])} size="icon-sm" type="button" variant="ghost"><Codicon name="chevron-right" /></Button></Tip>
            </div>
          </>}
          {selected && <section aria-label={labels.savedCopyPreview} className="space-y-2 text-sm">
            <h3 className="wrap-anywhere font-medium">{preview?.name ?? selected.name}</h3>
            {previewStatus === 'loading' && <p role="status">{labels.savedPreviewLoading}</p>}
            {previewStatus === 'failed' && <p role="alert">{labels.savedPreviewFailed}</p>}
            {preview && <>
              <p className="text-xs text-(--ui-text-secondary)">{labels.savedCopyReceived}: {date(preview.updatedAt)}</p>
              {preview.ended && <p>{labels.savedGroupEnded}</p>}
              {preview.incomplete && <p>{labels.savedCopyIncomplete}</p>}
              {preview.needsReview && <p>{labels.savedCopyNeedsReview}</p>}
              {preview.workUnknown && <p>{labels.savedWorkUnknown}</p>}
              {preview.reconciliationRequired && <p>{labels.savedWorkReconciliation}</p>}
              {preview.taskCount !== undefined && <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1">
                <dt>{labels.savedWorkTasks}</dt><dd>{new Intl.NumberFormat(locale).format(preview.taskCount)}</dd>
                <dt>{labels.savedWorkReceipts}</dt><dd>{new Intl.NumberFormat(locale).format(preview.receiptCount!)}</dd>
              </dl>}
            </>}
          </section>}
        </>}
      </DialogContent>
    </Dialog>}
  </>
}
