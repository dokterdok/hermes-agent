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

/** Display the older host's observed mode without converting its preference into new risk consent. */
function automaticSwitchValue(automatic: SuccessionAutomatic, enabledFallback: boolean): boolean {
  const ordinary = automatic.enabled ?? enabledFallback

  if (!twoHostAutomatic(automatic)) {return ordinary}

  if (automatic.careful_opt_in === null) {return automatic.enabled === true && automatic.mode === 'careful' && automatic.state !== 'off'}

  return automatic.careful_opt_in === true && ordinary
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
  const committed = automaticSwitchValue(automatic, enabledFallback)
  const requested = automatic.pending ?? committed

  const change = async (enabled: boolean, acceptRisk = false) => {
    if (pending.current || automatic.pending !== null) {return false}
    pending.current = true; setBusy(true); setFailed(false)

    try {
      await controller.setAutomatic(enabled, acceptRisk)

      return true
    } catch (error) {
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
    <p className="text-(--ui-text-tertiary)">{automatic.pending !== null ? automatic.pending ? words.turningOn : words.turningOff : words.automaticHelp}</p>
    {legacy && <div className="grid gap-2" role="status"><p>{labels.twoHostLegacy}</p>
      {ordinary && <Button disabled={busy || automatic.pending !== null} onClick={() => void change(false)} size="sm" variant="secondary">{labels.twoHostDisable}</Button>}
    </div>}
    {failed && <p className="text-destructive" role="alert">{words.changeFailed}</p>}
    <ConfirmDialog cancelLabel={labels.cancel} confirmLabel={labels.twoHostRiskConfirm} description={labels.twoHostRiskBody}
      onClose={() => setConfirming(false)} onConfirm={async () => {if (!await change(true, true)) {throw new Error(words.changeFailed)}}} open={confirming} title={labels.twoHostRiskTitle} />
  </>
}
