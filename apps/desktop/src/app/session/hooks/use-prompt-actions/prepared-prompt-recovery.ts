import { notifyError } from '@/store/notifications'

import { readPreparedSubmission } from './prepared-submissions'
import type { PreparedSubmission } from './prepared-submissions'

/** Accepted entries need no request; an ambiguous legacy write cannot be retried under a new identity. */
export async function readPreparedPromptRecovery(
  key: string,
  failureLabel: string
): Promise<{ entry?: PreparedSubmission; settled?: boolean }> {
  try {
    const entry = await readPreparedSubmission(key)

    if (entry?.acknowledged) {
      return { settled: true }
    }

    if (entry?.legacyAttempted) {
      return { settled: false }
    }

    return { entry }
  } catch (error) {
    notifyError(error, failureLabel)

    return { settled: false }
  }
}
