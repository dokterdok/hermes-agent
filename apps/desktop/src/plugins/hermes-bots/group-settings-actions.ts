import { host, translateNow } from '@hermes/plugin-sdk'

import { $botMeta, saveBotMeta } from './data'
import { setGroupChatHoldDetection, setGroupChatImage } from './group-chat'
import { compressGroupMemberHistory } from './group-compress'
import { botGroups, followGroupChat } from './group-membership'
import { botsText } from './i18n'
import { displayName } from './labels'
import { botRosterMeta } from './routing'
import type { GroupMember } from './types'

export interface GroupSettingsPatch {
  holdDetection?: boolean
  image?: null | string
}

export function applyGroupSettings(group: string, settings: GroupSettingsPatch) {
  if (settings.image !== undefined) {
    setGroupChatImage(group, settings.image)
  }

  if (settings.holdDetection !== undefined) {
    setGroupChatHoldDetection(group, settings.holdDetection)
  }
}

/** Only proven profile memberships change; a remote room-only seat needs no profile write. */
export async function renameGroupMemberships(
  oldName: string,
  members: GroupMember[],
  currentName: () => null | string
) {
  const failed: GroupMember[] = []

  for (const member of members) {
    const next = currentName()
    const groups = botGroups(botRosterMeta(member, $botMeta.get()))

    if (!next) {
      break
    }

    if (!member.name || !groups.includes(oldName)) {
      continue
    }

    const renamed = [...new Set(groups.map(name => (name === oldName ? next : name)))]
    const result = await saveBotMeta(member, { groups: renamed, group: renamed[0] || null })

    if (result.serverOutcome === 'failed') {
      failed.push(member)
    }
  }

  return failed
}

/** Retry current settings, never the old rename or a removed member's membership. */
export function reportGroupSettingsSyncFailure(group: string, members: GroupMember[]) {
  if (!members.length) {
    return
  }

  let current = group
  let retrying = false

  const binding = followGroupChat(group, name => {
    current = name
  })

  const b = botsText()

  const retry = async () => {
    if (retrying) {
      return
    }
    retrying = true
    const failed: GroupMember[] = []
    let unsupported = false

    try {
      for (const member of members) {
        if (!binding.isLive()) {
          break
        }
        const groups = botGroups(botRosterMeta(member, $botMeta.get()))

        if (!groups.includes(current)) {
          continue
        }
        const result = await saveBotMeta(member, { groups, group: groups[0] || null })

        if (result.serverOutcome === 'failed') {
          failed.push(member)
        }
        unsupported ||= result.serverOutcome === 'unsupported'
      }

      if (!binding.isLive()) {
        return
      }

      if (failed.length) {
        reportGroupSettingsSyncFailure(current, failed)
      } else if (unsupported) {
        host.notify({ kind: 'info', title: current, message: b.avatar.savedLocally })
      }
    } catch (error) {
      if (binding.isLive()) {
        host.notifyError(error, b.avatar.savedLocally)
      }
    } finally {
      binding.dispose()
    }
  }

  host.notify({
    kind: 'warning',
    title: group,
    message: b.avatar.savedLocally,
    detail: members.map(member => displayName(member, botRosterMeta(member, $botMeta.get()))).join(', '),
    action: {
      label: translateNow('common.retry'),
      onClick: () => {
        void retry()
      }
    },
    onDismiss: () => {
      if (!retrying) {
        binding.dispose()
      }
    }
  })
}

export async function summarizeGroupMember(group: string, member: GroupMember) {
  const b = botsText()
  const name = displayName(member, botRosterMeta(member, $botMeta.get()))
  host.notify({ kind: 'info', message: b.group.compressing(name) })

  try {
    const result = await compressGroupMemberHistory(group, member)

    const message = result.pending
      ? b.group.compressing(name)
      : result.compressed
        ? b.group.compressDone(name, result.compressed, result.lines.join('; '))
        : b.group.compressNothing(name)

    host.notify({ kind: result.compressed && !result.pending ? 'success' : 'info', message })
  } catch (error) {
    host.notify({
      kind: 'error',
      message: b.group.compressFailed(name, error instanceof Error ? error.message : String(error))
    })
  }
}
