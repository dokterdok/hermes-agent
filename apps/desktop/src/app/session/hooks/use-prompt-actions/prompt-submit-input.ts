import { translateNow } from '@/i18n'
import { optimisticAttachmentRef } from '@/lib/chat-runtime'
import { sanitizeComposerInput } from '@/lib/composer-input-sanitize'
import {
  isVoicePlaybackActive,
  markVoicePlaybackInterrupted,
  stopVoicePlayback,
  takeVoicePlaybackInterrupted
} from '@/lib/voice-playback'
import { freezeComposerTransportPayload } from '@/store/composer'
import type { ComposerAttachment } from '@/store/composer'
import { serverOwnsComposerQueue } from '@/store/composer-queue'
import { notify } from '@/store/notifications'
import { consumePendingCredentialWarning, requestDesktopOnboarding } from '@/store/onboarding'
import { $sessionStates } from '@/store/session-states'

import { isTargetSessionBusy } from './utils'
import type { SubmitTextOptions } from './utils'

/** One captured input snapshot supplies both optimistic display and the later synchronized wire context. */
export function captureComposerSubmitInput(
  rawText: string,
  options: SubmitTextOptions | undefined,
  scope: { readAttachments: () => ComposerAttachment[] }
) {
  const usingComposerAttachments = !options?.attachments

  // Drop undefined/null holes a session switch or draft restore can leave in
  // the attachments array (same bug class as AttachmentList #49624). Without
  // this, the sibling iterations below (a.kind / a.label / a.refText, and the
  // sync step) throw "Cannot read properties of undefined (reading 'refText')"
  // and break the chat surface.
  const attachments = (options?.attachments ?? scope.readAttachments()).filter((a): a is ComposerAttachment =>
    Boolean(a)
  )

  const titlePreview = attachments.find(a => typeof a.titlePreview === 'string' && a.titlePreview.trim())?.titlePreview

  // Queue drains already carry the frozen transport, independent of later terminal selections.
  let transportRaw = rawText
  let bubbleOverride = options?.displayText

  if (!options?.fromQueue) {
    const frozen = freezeComposerTransportPayload(rawText)

    if (frozen.missingLabels.length > 0) {
      notify({
        kind: 'warning',
        title: translateNow('composer.terminalSelectionMissingTitle'),
        message: translateNow('composer.terminalSelectionMissingBody')
      })

      return null
    }

    transportRaw = frozen.transportText

    if (!bubbleOverride && frozen.displayText !== frozen.transportText) {
      bubbleOverride = frozen.displayText
    }
  }

  const visibleText = sanitizeComposerInput(transportRaw).trim()
  const hasImage = attachments.some(a => a.kind === 'image')

  // Refs are recomputed after sync (file.attach rewrites @file: refs to
  // workspace-relative paths the remote gateway can resolve). Seed the
  // optimistic message with the pre-sync refs, then rewrite once synced.
  // Images use their bounded base64 thumbnail so the optimistic bubble
  // renders inline without embedding the full source — see optimisticAttachmentRef.
  const attachmentRefs = attachments.map(optimisticAttachmentRef).filter((r): r is string => Boolean(r))

  const buildContextText = (atts: ComposerAttachment[]): string => {
    // atts may be the post-sync array, which can reintroduce holes; filter
    // before touching a.refText / a.kind.
    const present = atts.filter((a): a is ComposerAttachment => Boolean(a))

    const contextRefs = present
      .map(a => a.refText)
      .filter(Boolean)
      .join('\n')

    return (
      [contextRefs, visibleText].filter(Boolean).join('\n\n') ||
      (present.some(a => a.kind === 'image') ? 'What do you see in this image?' : '')
    )
  }

  return {
    visibleText,
    usingComposerAttachments,
    attachments,
    titlePreview,
    bubbleOverride,
    hasImage,
    attachmentRefs,
    buildContextText
  }
}

/** Admission preflight keeps target busy/queue policy, voice barge-in and deferred provider warning in their original order. */
export function beginComposerSubmission(
  input: NonNullable<ReturnType<typeof captureComposerSubmitInput>>,
  options: SubmitTextOptions | undefined,
  activeSessionId: string | null,
  busy: boolean
) {
  const { visibleText, attachments, hasImage } = input
  // Queue drains fire on the busy→false settle edge, where busyRef (synced
  // from $busy by a separate effect) may still read true — honoring it would
  // bounce the drained send. The drain lock serializes them; the user path
  // keeps the guard so a stray Enter mid-turn can't double-submit.
  //
  // The guard reads the TARGET session's busy state (isTargetSessionBusy),
  // not the foreground flag: an explicit target (tile, queue drain) is
  // frequently not the session on screen, so the foreground flag would gate
  // one session's send on another session's turn.
  const hasSendable = Boolean(visibleText || attachments.length || hasImage)

  const guardSessionId = options?.sessionId ?? activeSessionId
  const serverQueue = serverOwnsComposerQueue(options?.storedSessionId ?? guardSessionId)

  const queueAdmission =
    serverQueue && Boolean(options?.fromQueue || isTargetSessionBusy($sessionStates.get(), guardSessionId, busy))

  if (
    !hasSendable ||
    (!serverQueue && !options?.fromQueue && isTargetSessionBusy($sessionStates.get(), guardSessionId, busy))
  ) {
    return null
  }

  // Typing barge-in: a new send silences any in-flight spoken reply.
  if (isVoicePlaybackActive()) {
    markVoicePlaybackInterrupted()
    stopVoicePlayback()
  }

  // The gateway already told us this profile has no usable provider (a
  // credential warning arrived with the session's runtime info, deferred
  // instead of popping onboarding on the mere profile switch). The user
  // is now actually trying to chat — THIS is the moment to open
  // onboarding, before a send the gateway said will fail. The draft
  // stays in the composer; once a provider is configured they just hit
  // Enter again.
  if (!options?.fromQueue) {
    const deferredCredentialWarning = consumePendingCredentialWarning()

    if (deferredCredentialWarning) {
      requestDesktopOnboarding(deferredCredentialWarning)

      return null
    }
  }

  // Barged mid-speech (here or via the voice loop's VAD)? Flag the submit
  // so the backend notes the interruption to the model.
  const interrupted = takeVoicePlaybackInterrupted()

  return { serverQueue, queueAdmission, interrupted }
}
