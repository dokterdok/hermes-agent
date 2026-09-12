import {
  Button, Codicon, Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, Switch, Tip
} from '@hermes/plugin-sdk'
import { useEffect, useId, useRef, useState } from 'react'

import type { FilesAuthority } from './canonical-files-client'
import { canonicalGroupRequest, type CanonicalGroupBinding } from './canonical-groups'
import { useCanonicalGroupLabels } from './canonical-group-labels'

interface HomeProps {
  binding: CanonicalGroupBinding
  authority: FilesAuthority
  name: string
}

interface HomeState {
  room_id: string
  enabled: boolean
  authority: { gateway_id: string; epoch: number }
}

function HomeDialog({ binding, authority, name, onClose }: HomeProps & { onClose: () => void }) {
  const labels = useCanonicalGroupLabels()
  const id = useId()
  const alive = useRef(true)
  const revision = useRef(0)
  const busyRef = useRef(false)
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const expected = { gateway_id: authority.gatewayId, epoch: authority.epoch }

  const request = async (value?: boolean) => {
    if (busyRef.current) {return}
    busyRef.current = true
    setBusy(true)
    const version = ++revision.current
    try {
      const result = await canonicalGroupRequest<HomeState>(binding,
        value === undefined ? 'groups.control.home.get' : 'groups.control.home.set', {
          room_id: binding.roomId, expected_authority: expected,
          ...(value === undefined ? {} : { enabled: value })
        })
      if (result.room_id !== binding.roomId || typeof result.enabled !== 'boolean' ||
          result.authority?.gateway_id !== expected.gateway_id || result.authority?.epoch !== expected.epoch ||
          (value !== undefined && result.enabled !== value)) {
        throw new Error('Unconfirmed room permission')
      }
      if (alive.current && version === revision.current) {setEnabled(result.enabled); setFailed(false)}
    } catch {
      if (alive.current && version === revision.current) {setEnabled(null); setFailed(true)}
    } finally {
      if (version === revision.current) {
        busyRef.current = false
        if (alive.current) {setBusy(false)}
      }
    }
  }

  useEffect(() => {
    alive.current = true
    void request()
    return () => { alive.current = false; revision.current++; busyRef.current = false }
    // Identity and authority are frozen by the keyed control.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <Dialog open onOpenChange={open => { if (!open) {onClose()} }}>
    <DialogContent bodyClassName="flex flex-col gap-4" className="max-w-md">
      <DialogHeader>
        <DialogTitle>{labels.messagingAccess}</DialogTitle>
        <DialogDescription><bdi>{name}</bdi></DialogDescription>
      </DialogHeader>
      <div className="flex items-center justify-between gap-4">
        <label htmlFor={id}>{labels.allowMessagingAccess}</label>
        <Switch checked={enabled === true} disabled={busy || enabled === null}
          id={id} onCheckedChange={value => void request(value)} />
      </div>
      <p className="text-sm text-(--ui-text-secondary)">{labels.messagingAccessScope}</p>
      {failed && <div role="alert" className="flex flex-col gap-2">
        <p>{labels.messagingAccessUnconfirmed}</p>
        <Button disabled={busy} onClick={() => void request()} type="button" variant="secondary">{labels.refresh}</Button>
      </div>}
    </DialogContent>
  </Dialog>
}

function HomeControl(props: HomeProps) {
  const labels = useCanonicalGroupLabels()
  const [open, setOpen] = useState(false)
  return <>
    <Tip label={labels.messagingAccess}>
      <Button aria-label={labels.messagingAccess} onClick={() => setOpen(true)} size="icon-sm" type="button" variant="ghost">
        <Codicon name="comment-discussion" />
      </Button>
    </Tip>
    {open && <HomeDialog {...props} onClose={() => setOpen(false)} />}
  </>
}

export function CanonicalGroupHome(props: HomeProps) {
  return <HomeControl {...props} key={JSON.stringify([props.binding, props.authority])} />
}
