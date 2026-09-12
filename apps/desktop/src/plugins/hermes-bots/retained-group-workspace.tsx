/** #97846 retained read view, deliberately independent of gateway capability. */
import { Button, Codicon, useValue } from '@hermes/plugin-sdk'
import { useLayoutEffect, useState } from 'react'

import { $groupChats } from './group-chat'
import {
  captureRetainedRoom,
  currentRetainedRoom,
  retainedEntries,
  retainedFileItem,
  type RetainedRoom,
  type RetainedRoomBinding,
  retainedSpeaker
} from './retained-group-files'
import { RetainedFileRow, RetainedFilesControl } from './retained-group-files-view'
import { useRetainedGroupLabels } from './retained-group-labels'

interface Props {
  binding?: RetainedRoomBinding | null
  group: string
  visible?: boolean
  onBack?: () => void
}

function RetainedRoomView({ group, room: initialRoom, binding: captured, visible = true, onBack }: Props & { room: RetainedRoom }) {
  const labels = useRetainedGroupLabels()
  const [binding] = useState(() => captured ?? captureRetainedRoom(group, initialRoom))
  const [intent, setIntent] = useState(() => new AbortController())
  const room = currentRetainedRoom(binding)
  const available = room !== null
  useLayoutEffect(() => {
    const controller = new AbortController()
    setIntent(controller)

    if (!visible || !available) {
      controller.abort()
    }

    return () => controller.abort()
  }, [visible, available])

  if (!visible) {
    return null
  }

  return (
    <section className="flex h-full min-h-0 flex-col gap-3 p-3">
      <header className="flex min-w-0 items-center gap-2">
        {onBack && (
          <Button aria-label={labels.back} onClick={onBack} size="icon-sm" type="button" variant="ghost">
            <Codicon name="arrow-left" />
          </Button>
        )}
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium">
          <bdi>{group}</bdi>
        </h2>
        <span className="text-xs text-(--ui-text-tertiary)">{labels.readOnly}</span>
        {room && <RetainedFilesControl binding={binding} />}
      </header>
      {!room ? (
        <p role="status">{labels.unavailable}</p>
      ) : (
        <>
          <div className="text-xs text-(--ui-text-tertiary)">
            <span>{labels.history}</span>
            {typeof room.hostedConnectionId === 'string' && (
              <p>
                {labels.source}: <bdi>{room.hostedConnectionId}</bdi>
              </p>
            )}
          </div>
          <div aria-label={labels.history} className="min-h-0 flex-1 overflow-y-auto" role="log">
            {retainedEntries(room).map((entry, index) => (
              <article className="min-w-0 py-3" key={`${entry.id || entry.eventId || 'retained'}:${index}`}>
                <header className="flex flex-wrap items-baseline gap-2 text-xs">
                  <strong>
                    <bdi>{retainedSpeaker(entry.from)}</bdi>
                  </strong>
                  {Number.isFinite(entry.at) && (
                    <time className="text-(--ui-text-tertiary)" dateTime={new Date(entry.at).toISOString()}>
                      {new Date(entry.at).toLocaleString(labels.locale)}
                    </time>
                  )}
                </header>
                <p className="whitespace-pre-wrap break-words text-sm">{entry.text}</p>
                {!!entry.images?.length && (
                  <div role="list">
                    {entry.images.slice(0, 8).map((_, position) => (
                      <RetainedFileRow
                        item={retainedFileItem(binding, entry, position, `transcript:${index}:${position}`)}
                        key={position}
                        signal={intent.signal}
                      />
                    ))}
                  </div>
                )}
              </article>
            ))}
            {!room.log.length && <p className="py-4 text-sm text-(--ui-text-tertiary)">{labels.empty}</p>}
          </div>
        </>
      )}
    </section>
  )
}

export function RetainedGroupWorkspace(props: Props) {
  const rooms = useValue($groupChats)
  const labels = useRetainedGroupLabels()
  const room = props.binding === undefined ? rooms[props.group] : props.binding && currentRetainedRoom(props.binding)

  if (!room || room.tombstone) {
    return (
      <p className="p-3" role="status">
        {labels.unavailable}
      </p>
    )
  }

  return <RetainedRoomView {...props} key={props.group} room={room} />
}
