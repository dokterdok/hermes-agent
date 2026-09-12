"""Get the exact selected Bot reply as a native document, not a truncated preview."""
import hashlib
import json

from gateway.group_chat_files import delivery_keys, room_key
from gateway.group_chat_messages import text
from gateway.group_chat_policy import receiving_group_context
from gateway.group_chat_work import run_group_read
from gateway.group_home_consent import require_current
from gateway.hosted_room_file_delivery import Document, deliver_document, native_document_limit
from gateway.platforms.base import _thread_metadata_for_event


async def get_reply(runner, event, backend, room, stamp, *, selected=None):
    context = receiving_group_context(runner, event.source)
    require_current(runner, event, stamp)
    native_document_limit(context.adapter, event.source)
    item = selected or await run_group_read(lambda: backend.latest_reply(room=room))
    selection = hashlib.sha256(json.dumps([room_key(room), item['event_id'], 'reply'], separators=(',', ':')).encode()).hexdigest()
    key, scope = delivery_keys(runner, event, backend, stamp, selection)
    def load(maximum):
        current = backend.read_reply(room=room, event_id=item['event_id'])
        if current != item:
            raise PermissionError('Reply changed')
        return Document('reply.md', item['text'].encode('utf-8'))
    async def recheck():
        require_current(runner, event, stamp)
        current = await run_group_read(lambda: backend.read_reply(room=room, event_id=item['event_id']))
        if current != item:
            raise PermissionError('Reply changed')
        require_current(runner, event, stamp)
    outcome = await deliver_document(db_path=backend.db_path, key=key, scope=scope, adapter=context.adapter,
        source=event.source, load=load, recheck=recheck, metadata=_thread_metadata_for_event(event) or {}, reply_to=event.message_id)
    require_current(runner, event, stamp)
    return text('group_files', outcome if outcome in {'delivered', 'unknown', 'busy', 'failed'} else 'error')
