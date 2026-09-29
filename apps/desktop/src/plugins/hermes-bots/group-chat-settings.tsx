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
import { useEffect, useId, useRef, useState } from 'react'

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

/** The room opened in this dialog. A later row with the same display name is
 *  a different room when its id disagrees. */
interface SettingsIdentity {
  hosted: boolean
  name: string
  present: boolean
  roomId: null | string
}

interface LocatedRoom {
  key: string
  room: GroupChatRoom
}

const RENAME_BLOCKED_STATES = new Set(['queued', 'read-only', 'sending', 'stopping', 'working'])

function captureIdentity(name: string, room: GroupChatRoom | undefined): SettingsIdentity {
  return {
    hosted: Boolean(groupChatHostedGateway(room)),
    name,
    present: Boolean(room) && room?.tombstone !== true,
    roomId: typeof room?.roomId === 'string' && room.roomId ? room.roomId : null
  }
}

function sameOpenedRoom(room: GroupChatRoom | undefined, identity: SettingsIdentity) {
  if (!identity.present || !room || room.tombstone) {
    return false
  }

  if (identity.roomId) {
    return room.roomId === identity.roomId
  }

  return !room.roomId
}

/** Resolve the opened room. A hosted id follows a rename of the map key.
 *  A legacy room without an id stays on its original name unless this save
 *  just moved that same row. */
function locateOpenedRoom(
  rooms: Record<string, GroupChatRoom>,
  identity: SettingsIdentity,
  renamedTo?: null | string
): LocatedRoom | null {
  if (!identity.present) {
    return null
  }

  if (identity.roomId) {
    const matches = Object.entries(rooms).filter(([, room]) => sameOpenedRoom(room, identity))

    return matches.length === 1 ? { key: matches[0][0], room: matches[0][1] } : null
  }

  if (renamedTo && rooms[renamedTo] && sameOpenedRoom(rooms[renamedTo], identity) && !rooms[identity.name]) {
    return { key: renamedTo, room: rooms[renamedTo] }
  }

  return sameOpenedRoom(rooms[identity.name], identity) ? { key: identity.name, room: rooms[identity.name] } : null
}

/** Hosted rename is a gateway command and is refused while the room is busy
 *  or read-only. The picture is a local identity field and is not part of
 *  that restriction. Hold detection and history compression only affect the
 *  classic Desktop round engine. */
function hostedRenameReason(room: GroupChatRoom, copy: { settingsRenameBusy: string; settingsRenameReadOnly: string }) {
  if (!groupChatHostedGateway(room)) {
    return null
  }

  const state = String(room.hostedStatus?.state || '')

  if (state === 'read-only') {
    return copy.settingsRenameReadOnly
  }

  if (room.running === true || RENAME_BLOCKED_STATES.has(state)) {
    return copy.settingsRenameBusy
  }

  return null
}

/** Edit an existing group chat's name and picture. Renames re-key the room
 *  and every local member's membership (renameGroupChat); the picture rides
 *  the room record. Both apply on Save so a cancelled dialog changes nothing.
 *  A blocked rename does not block other settings the room still accepts. */
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
  const reasonId = useId()
  const rooms: Record<string, GroupChatRoom> = useValue($groupChats)
  const identityRef = useRef<SettingsIdentity | null>(null)
  const wasOpen = useRef(false)
  const nameEdited = useRef(false)
  const [name, setName] = useState(group)
  const [image, setImage] = useState<null | string>(null)
  const [holdDetection, setHoldDetection] = useState(true)
  const [compressing, setCompressing] = useState<null | string>(null)

  // Pins the room this dialog opened. A later row with the same name is a
  // different room and must not receive this save. This is not a live mirror
  // of $groupChats; callbacks read the atom directly before they write.
  // eslint-disable-next-line no-restricted-syntax -- open-time identity pin, not an atom mirror
  useEffect(() => {
    if (!open) {
      wasOpen.current = false
      identityRef.current = null
      nameEdited.current = false

      return
    }

    const incoming = $groupChats.get()[group]
    const next = captureIdentity(group, incoming)
    const reopened = !wasOpen.current
    wasOpen.current = true

    const same = !reopened && Boolean(identityRef.current?.roomId) && identityRef.current?.roomId === next.roomId

    if (same && identityRef.current) {
      identityRef.current = { ...identityRef.current, hosted: next.hosted, name: group }

      return
    }

    identityRef.current = next
    nameEdited.current = false
    setName(group)
    setImage(incoming?.image || null)
    setHoldDetection(incoming?.holdDetection !== false)
  }, [open, group])

  const identity = identityRef.current ?? captureIdentity(group, rooms[group])
  const located = locateOpenedRoom(rooms, identity)
  const showClassic = Boolean(located) && !groupChatHostedGateway(located?.room)
  const lockReason = located ? null : b.group.settingsRoomUnavailable
  const nameReason = lockReason || (located ? hostedRenameReason(located.room, b.group) : null)
  const requested = name.trim().slice(0, 64)
  const imageDirty = Boolean(located) && (image || null) !== (located?.room.image || null)
  const holdDirty = showClassic && Boolean(located) && holdDetection !== (located?.room.holdDetection !== false)
  const canSave = Boolean(located) && ((!nameReason && requested.length > 0) || imageDirty || holdDirty)
  const continuityRoom = located?.room
  const hostedState = String(continuityRoom?.hostedStatus?.state || '')
  const continuity = groupChatContinuityMode(continuityRoom)

  // Per-member "Compress history" (#102291): the member's hidden plumbing
  // session is reachable from nowhere else, so the room that shows the
  // symptom (empty replies) owns the repair. One member at a time — the
  // gateway refuses a second compress while one holds the lock.
  const compressMember = async (member: GroupMember) => {
    const pinned = identityRef.current
    const target = pinned ? locateOpenedRoom($groupChats.get(), pinned) : null

    if (!target || groupChatHostedGateway(target.room)) {
      return
    }

    const memberName = displayName(member, botRosterMeta(member, $botMeta.get()))
    setCompressing(groupMemberKey(member))
    host.notify({ kind: 'info', message: b.group.compressing(memberName) })

    try {
      const outcome = await compressGroupMemberHistory(target.key, member)

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
    const pinned = identityRef.current

    if (!pinned?.present) {
      host.notify({ kind: 'error', message: b.group.settingsRoomUnavailable })

      return
    }

    const currentRooms = $groupChats.get()
    const opened = locateOpenedRoom(currentRooms, pinned)
    const renameReason = opened ? hostedRenameReason(opened.room, b.group) : null
    const nextName = name.trim().slice(0, 64)
    const pictureDirty = Boolean(opened) && (image || null) !== (opened?.room.image || null)
    const classic = Boolean(opened) && !groupChatHostedGateway(opened?.room)
    const holdChanged = classic && holdDetection !== (opened?.room.holdDetection !== false)
    const allowed = Boolean(opened) && ((!renameReason && nextName.length > 0) || pictureDirty || holdChanged)

    if (!opened) {
      host.notify({ kind: 'error', message: b.group.settingsRoomUnavailable })

      return
    }

    if (!allowed) {
      return
    }

    let renameFailed = false
    let key = opened.key
    const shouldRename = !renameReason && nameEdited.current && nextName.length > 0 && nextName !== opened.key

    if (shouldRename) {
      try {
        const renamed = await renameGroupChat(opened.key, nextName, members)
        const after = locateOpenedRoom($groupChats.get(), pinned, renamed)

        if (!after) {
          host.notify({ kind: 'error', message: b.group.settingsRoomUnavailable })

          return
        }

        key = after.key

        if (renamed === null || renamed !== after.key) {
          renameFailed = true

          if (renamed !== null) {
            host.notify({ kind: 'error', message: b.group.settingsSaveFailed })
          }
        }
      } catch {
        host.notify({ kind: 'error', message: b.group.settingsSaveFailed })

        return
      }
    }

    const latest = locateOpenedRoom($groupChats.get(), pinned, key)

    if (!latest) {
      host.notify({ kind: 'error', message: b.group.settingsRoomUnavailable })

      return
    }

    key = latest.key

    if ((image || null) !== (latest.room.image || null)) {
      if (!sameOpenedRoom($groupChats.get()[key], pinned)) {
        host.notify({ kind: 'error', message: b.group.settingsSaveFailed })

        return
      }

      setGroupChatImage(key, image)
      const written = $groupChats.get()[key]

      if (!sameOpenedRoom(written, pinned) || (written?.image || null) !== (image || null)) {
        host.notify({ kind: 'error', message: b.group.settingsSaveFailed })

        return
      }
    }

    const afterPicture = $groupChats.get()[key]

    if (
      afterPicture &&
      !groupChatHostedGateway(afterPicture) &&
      holdDetection !== (afterPicture.holdDetection !== false)
    ) {
      if (!sameOpenedRoom(afterPicture, pinned)) {
        host.notify({ kind: 'error', message: b.group.settingsSaveFailed })

        return
      }

      setGroupChatHoldDetection(key, holdDetection)
      const written = $groupChats.get()[key]

      if (!sameOpenedRoom(written, pinned) || (written?.holdDetection !== false) !== holdDetection) {
        host.notify({ kind: 'error', message: b.group.settingsSaveFailed })

        return
      }
    }

    const renameStillBlocked = nameEdited.current && nextName.length > 0 && nextName !== key && Boolean(renameReason)

    if (renameFailed || renameStillBlocked) {
      return
    }

    onClose()

    if (key !== group) {
      onRenamed?.(key)
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
        {lockReason ? (
          <p className="text-xs text-(--ui-text-tertiary)" id={reasonId} role="alert">
            {lockReason}
          </p>
        ) : null}
        {continuityRoom ? (
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
        ) : null}
        <fieldset
          aria-describedby={lockReason ? reasonId : undefined}
          className="m-0 min-w-0 border-0 p-0"
          disabled={Boolean(lockReason)}
        >
          <GroupImageControls
            image={image}
            onImage={setImage}
            seedMembers={(members || []).map(member => member.name)}
            seedName={name.trim() || group}
          />
        </fieldset>
        <form
          onSubmit={event => {
            event.preventDefault()
            void save()
          }}
        >
          <Input
            aria-describedby={nameReason ? reasonId : undefined}
            aria-disabled={nameReason ? true : undefined}
            aria-label={b.group.nameLabel}
            autoFocus
            maxLength={64}
            onChange={event => {
              if (nameReason) {
                return
              }

              nameEdited.current = true
              setName(event.target.value)
            }}
            readOnly={Boolean(nameReason)}
            value={name}
          />
        </form>
        {nameReason && !lockReason ? (
          <p className="text-xs text-(--ui-text-tertiary)" id={reasonId} role="status">
            {nameReason}
          </p>
        ) : null}
        {showClassic ? (
          <ToggleRow
            checked={holdDetection}
            description={b.group.holdDetectionHint}
            disabled={Boolean(lockReason)}
            label={b.group.holdDetection}
            onChange={setHoldDetection}
          />
        ) : null}
        {showClassic && (members || []).length > 0 ? (
          <ul className="flex flex-col gap-1" data-testid="group-settings-members">
            {(members || []).map(member => {
              const key = groupMemberKey(member)

              return (
                <li className="flex items-center justify-between gap-2 text-sm" key={key}>
                  <span className="truncate">{displayName(member, botRosterMeta(member, $botMeta.get()))}</span>
                  <Tip label={b.group.compressHistoryHint(member.name)}>
                    <Button
                      aria-label={`${b.group.compressHistory}: ${member.name}`}
                      disabled={compressing !== null || Boolean(lockReason)}
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
          <Button
            aria-describedby={!canSave && (lockReason || nameReason) ? reasonId : undefined}
            disabled={!canSave}
            onClick={() => void save()}
          >
            {t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
