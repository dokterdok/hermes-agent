import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, gatewayActivationEpoch, host } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'

import { resumeCanonicalGroupCreate } from './canonical-group-create'
import type { PreparedCanonicalGroupCreate } from './canonical-group-create'
import { useCanonicalGroupLabels } from './canonical-group-labels'
import { registerCanonicalGroup } from './canonical-group-registry'

export function CanonicalGroupCreateRecovery({ entry, open, onClose, onCreated }: {
  entry: PreparedCanonicalGroupCreate; open: boolean; onClose: () => void; onCreated?: (key: string) => void
}) {
  const labels = useCanonicalGroupLabels()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const current = useRef(0)
  const running = useRef<symbol | null>(null)
  // eslint-disable-next-line no-restricted-syntax -- Invalidate closed/replaced dialog attempts, not a mirror of reactive store values.
  useEffect(() => {
    current.current++
    running.current = null
    setBusy(false)
    setError('')
    return () => { current.current++ }
  }, [entry, open])

  const resume = async () => {
    const activationEpoch = gatewayActivationEpoch()
    const sourceCurrent = () => gatewayActivationEpoch() === activationEpoch
      && entry.binding.connectionId === host.state.connectionId.get()
      && entry.binding.profile === host.state.profile.get() && host.state.gateway.get() === 'open'

    if (!open || running.current || !sourceCurrent()) {return}
    const attempt = Symbol('group-create')
    running.current = attempt
    const version = current.current
    setBusy(true)
    setError('')
    try {
      const created = await resumeCanonicalGroupCreate(entry.binding, entry.binding.roomId)
      if (current.current !== version || !open || !sourceCurrent()) {return}
      const key = registerCanonicalGroup(created.binding, created.room)
      onClose()
      onCreated?.(key)
    } catch (cause) {
      if (current.current === version && sourceCurrent()) {setError(cause instanceof Error ? cause.message : String(cause))}
    } finally {
      if (running.current === attempt) {running.current = null}
      if (current.current === version) {setBusy(false)}
    }
  }

  return <Dialog onOpenChange={value => { if (!value) {onClose()} }} open={open}>
    <DialogContent className="max-w-md">
      <DialogHeader>
        <DialogTitle>{labels.savedSetup}</DialogTitle>
        <DialogDescription>{labels.savedSetupDetail}</DialogDescription>
      </DialogHeader>
      <p className="break-words font-medium">{entry.params.name}</p>
      <ul>{entry.params.members.map(member => <li key={member.member_id}>{member.display_name || member.handle}</li>)}</ul>
      {error && <p role="alert">{error}</p>}
      <DialogFooter>
        <Button onClick={onClose} variant="secondary">{labels.cancel}</Button>
        <Button disabled={busy} onClick={() => void resume()}>{labels.continueSetup}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
