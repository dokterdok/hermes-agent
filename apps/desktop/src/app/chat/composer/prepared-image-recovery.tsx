import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { claimPreparedSubmission, listPreparedDrafts } from '@/app/session/hooks/use-prompt-actions/prepared-submissions'
import { captureSubmissionDestination } from '@/app/session/hooks/use-prompt-actions/submission-destination'
import type { GatewayRequest } from '@/app/session/hooks/use-prompt-actions/utils'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import type { ComposerAttachment } from '@/store/composer'
import { notifyError } from '@/store/notifications'

interface Props {
  sessionKey: string | null
  request: GatewayRequest
  occupied: boolean
  onRestore: (text: string, attachments: ComposerAttachment[]) => void
}

export function PreparedImageRecovery({ sessionKey, request, occupied, onRestore }: Props) {
  const { t } = useI18n()
  const [drafts, setDrafts] = useState<Awaited<ReturnType<typeof listPreparedDrafts>>>([])
  const [readError, setReadError] = useState<string | null>(null)
  const [readAttempt, setReadAttempt] = useState(0)
  const scopeKey = captureSubmissionDestination(sessionKey, request).scopeKey
  const current = useRef({ sessionKey, scopeKey, occupied, onRestore })
  current.current = { sessionKey, scopeKey, occupied, onRestore }
  const mounted = useRef(true)
  const claiming = useRef(false)
  const [pending, setPending] = useState(false)
  // The claim callback must see disposal in the unmount commit, not later.
  useLayoutEffect(() => {
    mounted.current = true

    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    let cancelled = false
    setDrafts([])
    setReadError(null)

    if (sessionKey) {
      void listPreparedDrafts(sessionKey, scopeKey).then(entries => {
        if (!cancelled) { setDrafts(entries) }
      }).catch(error => {
        if (!cancelled) {setReadError(error instanceof Error ? error.message : t.composer.draftReadFailed)}
      })
    }

    return () => { cancelled = true }
  }, [sessionKey, scopeKey, t.composer.draftReadFailed, readAttempt])

  return <>
    {readError && <div className="grid gap-2 rounded-md border bg-background px-3 py-2 text-xs" role="alert">
      <p>{t.composer.draftReadFailed}</p>
      <Button className="justify-self-start" onClick={() => setReadAttempt(value => value + 1)} size="sm" type="button" variant="outline">{t.common.retry}</Button>
      <details className="text-muted-foreground"><summary>{t.notifications.details}</summary><pre className="whitespace-pre-wrap break-words pt-1">{readError}</pre></details>
    </div>}
    {drafts.map(draft => (
    <div className="flex items-center gap-2 rounded-md border bg-background px-3 py-2 text-xs" key={draft.key}>
      <span className="min-w-0 flex-1 truncate">{draft.text || draft.attachments.map(attachment => attachment.label).join(', ')}</span>
      <Button disabled={occupied || pending} onClick={async () => {
        const isCurrent = () => mounted.current && current.current.sessionKey === sessionKey &&
          current.current.scopeKey === scopeKey && !current.current.occupied && current.current.onRestore === onRestore

        if (claiming.current || !isCurrent()) {return}
        claiming.current = true
        setPending(true)

        try {
          await claimPreparedSubmission(draft.key, draft.expected)

          if (!isCurrent()) {return}
          onRestore(draft.text, draft.attachments)
          setDrafts(current => current.filter(entry => entry.key !== draft.key))
        } catch (error) {if (isCurrent()) {notifyError(error, t.composer.restoreImageDraft)}}
        finally {
          claiming.current = false

          if (mounted.current) {setPending(false)}
        }
      }} size="sm" type="button" variant="outline">{t.composer.restoreImageDraft}</Button>
    </div>
  ))}
  </>
}
