import {
  Button,
  Codicon,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  host,
  Input,
  Tip,
  ToggleRow,
  useI18n,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'

import { $botMeta } from './data'
import type { GroupChatRoom } from './group-chat'
import {
  $groupChats,
  groupChatContinuityMode,
  groupChatHostedGateway,
  setGroupChatHoldDetection,
  setGroupChatImage
} from './group-chat'
import { GroupImageControls } from './group-chat-parts'
import { renameGroupChat } from './group-chat-view'
import { compressGroupMemberHistory } from './group-compress'
import { groupMemberKey } from './group-membership'
import { useBots } from './i18n'
import { displayName } from './labels'
import { botRosterMeta } from './routing'
import type { GroupMember } from './types'

interface GroupChatSettingsDialogProps {
  group: string
  members?: GroupMember[]
  onClose: () => void
  onManageMembers?: () => void
  onRenamed?: (group: string) => void
  open: boolean
}

/** Edit an existing group chat's name and picture. Renames re-key the room
 *  and every local member's membership (renameGroupChat); the picture rides
 *  the room record. Both apply on Save so a cancelled dialog changes nothing. */
export function GroupChatSettingsDialog({
  group,
  members,
  open,
  onClose,
  onManageMembers,
  onRenamed
}: GroupChatSettingsDialogProps) {
  const { t } = useI18n()
  const b = useBots()
  const rooms: Record<string, GroupChatRoom> = useValue($groupChats)
  const room = rooms[group] || {}
  const current = room.image || null
  const hosted = Boolean(groupChatHostedGateway(room))
  const hostedState = String(room.hostedStatus?.state || '')

  const renameBlocked =
    hosted && (room.running === true || ['queued', 'read-only', 'sending', 'stopping', 'working'].includes(hostedState))

  const continuity = groupChatContinuityMode(room)
  const currentHoldDetection = room.holdDetection !== false
  const [name, setName] = useState(group)
  const [image, setImage] = useState(current)
  const [holdDetection, setHoldDetection] = useState(currentHoldDetection)
  const [compressing, setCompressing] = useState<null | string>(null)
  useEffect(() => {
    if (open) {
      setName(group)
      setImage(current)
      setHoldDetection(currentHoldDetection)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, group])

  // Per-member "Compress history" (#102291): the member's hidden plumbing
  // session is reachable from nowhere else, so the room that shows the
  // symptom (empty replies) owns the repair. One member at a time — the
  // gateway refuses a second compress while one holds the lock.
  const compressMember = async (member: GroupMember) => {
    const memberName = displayName(member, botRosterMeta(member, $botMeta.get()))
    setCompressing(groupMemberKey(member))
    host.notify({ kind: 'info', message: b.group.compressing(memberName) })

    try {
      const outcome = await compressGroupMemberHistory(group, member)

      if (outcome.compressed === 0 && outcome.pending === 0) {
        host.notify({ kind: 'info', message: b.group.compressNothing(memberName) })
      } else {
        host.notify({
          kind: 'success',
          message: b.group.compressDone(memberName, outcome.compressed + outcome.pending, outcome.lines.join('; '))
        })
      }
    } catch (error) {
      host.notify({
        kind: 'error',
        message: b.group.compressFailed(memberName, error instanceof Error ? error.message : String(error))
      })
    } finally {
      setCompressing(null)
    }
  }

  const save = async () => {
    if (renameBlocked) {
      return
    }

    const finalName = await renameGroupChat(group, name, members)

    if (finalName === null) {
      return // collision — dialog stays open for a different name
    }

    if (image !== current) {
      setGroupChatImage(finalName, image)
    }

    if (holdDetection !== currentHoldDetection) {
      setGroupChatHoldDetection(finalName, holdDetection)
    }

    onClose()

    if (finalName !== group) {
      onRenamed?.(finalName)
    }
  }

  return (
    <Dialog
      onOpenChange={value => {
        if (!value) {
          onClose()
        }
      }}
      open={open}
    >
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{b.group.settingsTitle}</DialogTitle>
          <DialogDescription>{b.group.settingsDesc}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-0.5 text-sm">
          <div className="font-medium text-(--ui-text-primary)">
            {hostedState === 'read-only'
              ? b.group.continuityReadOnlyTitle
              : continuity === 'desktop'
                ? b.group.continuityDesktopTitle
                : b.group.continuityOnTitle}
          </div>
          <div className="text-xs text-(--ui-text-tertiary)">
            {hostedState === 'read-only'
              ? b.group.continuityReadOnlyDesc
              : continuity === 'desktop'
                ? b.group.continuityDesktopDesc
                : b.group.continuityOnDesc}
          </div>
        </div>
        <GroupImageControls
          image={image}
          onImage={setImage}
          seedMembers={(members || []).map(member => member.name)}
          seedName={name.trim() || group}
        />
        <form
          onSubmit={event => {
            event.preventDefault()
            void save()
          }}
        >
          <Input
            aria-label={b.group.nameLabel}
            autoFocus
            disabled={renameBlocked}
            maxLength={64}
            onChange={event => setName(event.target.value)}
            value={name}
          />
        </form>
        <ToggleRow
          checked={holdDetection}
          description={b.group.holdDetectionHint}
          label={b.group.holdDetection}
          onChange={setHoldDetection}
        />
        {(members || []).length > 0 ? (
          <ul className="flex flex-col gap-1" data-testid="group-settings-members">
            {(members || []).map(member => {
              const key = groupMemberKey(member)

              return (
                <li className="flex items-center justify-between gap-2 text-sm" key={key}>
                  <span className="truncate">{displayName(member, botRosterMeta(member, $botMeta.get()))}</span>
                  <Tip label={b.group.compressHistoryHint(member.name)}>
                    <Button
                      aria-label={`${b.group.compressHistory}: ${member.name}`}
                      disabled={compressing !== null}
                      onClick={() => void compressMember(member)}
                      size="sm"
                      variant="secondary"
                    >
                      <Codicon name={compressing === key ? 'loading' : 'fold'} spinning={compressing === key} />
                      {b.group.compressHistory}
                    </Button>
                  </Tip>
                </li>
              )
            })}
          </ul>
        ) : null}
        {onManageMembers ? (
          <Button
            className="w-fit"
            onClick={() => {
              onClose()
              onManageMembers()
            }}
            size="sm"
            variant="secondary"
          >
            <Codicon name="organization" />
            {`Manage members (${(members || []).length})…`}
          </Button>
        ) : null}
        <DialogFooter>
          <Button onClick={onClose} variant="secondary">
            {t.common.cancel}
          </Button>
          <Button disabled={!name.trim() || renameBlocked} onClick={() => void save()}>
            {t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
