import { Button, ConfirmDialog, Switch } from '@hermes/plugin-sdk'
import { useRef, useState } from 'react'

import { useCanonicalGroupLabels } from './canonical-group-labels'
import { successionFailure } from './canonical-group-succession'
import type { SuccessionAutomatic } from './canonical-group-succession'
import type { SuccessionController } from './canonical-group-succession-state'
import { useBots } from './i18n'

export function twoHostAutomatic(automatic: SuccessionAutomatic): boolean {
  return automatic.voters === 2 || automatic.mode === 'careful'
}

/** Ordinary automatic preference never serves as consent for two-computer execution risk. */
export function AutomaticMoveSetting({automatic, controller, offered, enabledFallback}: {
  automatic: SuccessionAutomatic; controller: SuccessionController; offered: boolean; enabledFallback: boolean
}) {
  const words = useBots().succession
  const labels = useCanonicalGroupLabels()
  const [confirming, setConfirming] = useState(false)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const two = twoHostAutomatic(automatic)
  const legacy = two && automatic.careful_opt_in === null
  const ordinary = automatic.enabled ?? enabledFallback
  const committed = two ? automatic.careful_opt_in === true && ordinary : ordinary
  const requested = two ? committed : automatic.pending ?? committed

  const change = async (enabled: boolean, acceptRisk = false) => {
    if (pending.current) {return}
    pending.current = true; setBusy(true); setFailed(false)

    try {await controller.setAutomatic(enabled, acceptRisk);

 return true} catch (error) {
      if (!acceptRisk && successionFailure(error)?.reason === 'careful_confirmation_required') {setConfirming(true); controller.refresh()}
      else {setFailed(true)}

      return false
    } finally {pending.current = false; setBusy(false)}
  }

  if (!offered) {return null}

  return <>
    <label className="flex items-center gap-2 pt-1">
      <Switch aria-label={words.automaticSwitch} checked={requested} disabled={busy || legacy || automatic.pending !== null}
        onCheckedChange={enabled => {if (enabled && two) {setConfirming(true)} else {void change(enabled)}}} size="xs" />
      {words.automaticSwitch}
    </label>
    <p className="text-(--ui-text-tertiary)">{automatic.pending !== null ? ordinary ? words.turningOn : words.turningOff : words.automaticHelp}</p>
    {legacy && <div className="grid gap-2" role="status"><p>{labels.twoHostLegacy}</p>
      {ordinary && <Button disabled={busy} onClick={() => void change(false)} size="sm" variant="secondary">{labels.twoHostDisable}</Button>}
    </div>}
    {failed && <p className="text-destructive" role="alert">{words.changeFailed}</p>}
    <ConfirmDialog cancelLabel={labels.cancel} confirmLabel={labels.twoHostRiskConfirm} description={labels.twoHostRiskBody}
      onClose={() => setConfirming(false)} onConfirm={async () => {if (!await change(true, true)) {throw new Error(words.changeFailed)}}} open={confirming} title={labels.twoHostRiskTitle} />
  </>
}
