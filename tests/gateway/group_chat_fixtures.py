"""Shared doubles for messaging control of canonical Group Chats: a receiving Bot, a runner
with one real canonical authority store, and messages from people in private or shared chats."""
from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from hermes_state_runtime import begin_runtime_epoch

OWNER = 'uid:501'


class Bot:
    """The adapter that received a command; only its config and sends matter here."""
    typed_command_prefix = '/'

    def __init__(self, *, dm_admins=('alice',), group_admins=('alice', 'bob')):
        self.config = PlatformConfig(enabled=True, extra={
            'allow_admin_from': list(dm_admins), 'group_allow_admin_from': list(group_admins)})
        self.sent = []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return SendResult(success=True)


class Buttons(Bot):
    """A Bot whose adapter shows native buttons (Telegram, Discord, Slack, WhatsApp Cloud)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.offers = []

    async def send_group_actions(self, chat_id, text, buttons, metadata=None):
        self.offers.append(SimpleNamespace(chat_id=chat_id, text=text, buttons=list(buttons)))
        return SimpleNamespace(success=True)


def runner_for(authority, bot):
    runner = SimpleNamespace(session_authority=authority, _primary_profile_name='default')
    runner._transport_owner = lambda source: (bot, None)
    runner._typed_command_prefix_for = lambda platform: '/'
    runner._thread_metadata_for_source = lambda source: (
        {'thread_id': source.thread_id} if source.thread_id else None)
    authority.runner = runner
    return runner


def authority_for(home, db):
    return SimpleNamespace(db=db, profile_id=str(home), instance_id='test', events={},
                           epoch=begin_runtime_epoch(db, instance_id='test'))


def finish_approval_task(service, room_id):
    from gateway import hosted_room_driver as driver

    attempt = getattr(service, '_messaging_fixture_attempts', {}).pop(room_id, None)
    if attempt is not None:
        driver.settle_task(service.db_path, attempt, settlement_id='fixture:' + attempt.identity.task_id,
                           status='settled', result={}, clock=service.runtime.clock)


def start_approval_task(service, room_id, member_id, task_id):
    """Give a fixture prompt the same durable task/lease fence as a real room turn."""
    from gateway import hosted_room_driver as driver, hosted_rooms

    attempts = getattr(service, '_messaging_fixture_attempts', {})
    service._messaging_fixture_attempts = attempts
    previous = attempts.get(room_id)
    if previous is not None and previous.identity.task_id == task_id:
        return previous
    finish_approval_task(service, room_id)
    room = hosted_rooms.room_state(service.db_path, room_id=room_id)
    member = next(row for row in room['members'] if row['member_id'] == member_id)
    event = hosted_rooms.append_event(
        service.db_path, room_id=room_id, event_id='fixture:' + task_id, kind='message.user',
        actor={'kind': 'user', 'id': OWNER}, payload={'text': 'Approval fixture', 'thread_id': task_id},
        authority_gateway_id=room['authority_gateway_id'], authority_epoch=room['authority_epoch'])
    identity = driver.TaskIdentity(room_id, task_id, task_id, 'turn:' + task_id)
    admitted = driver.admit_task(service.db_path, identity, payload={
        'target_profile': member['profile'], 'target_member_id': member_id,
        'source_event_seq': event['seq'], 'prompt': 'Approval fixture'}, clock=service.runtime.clock)
    lease = driver.acquire_lease(service.db_path, room_id=room_id,
        gateway_id=room['authority_gateway_id'], authority_epoch=room['authority_epoch'],
        process_generation=service.runtime.process_generation, ttl_seconds=120, clock=service.runtime.clock)
    attempt = driver.start_task(service.db_path, identity, lease,
        expected_cancel_generation=admitted['cancel_generation'], clock=service.runtime.clock)
    attempts[room_id] = attempt
    return attempt


def message(text, *, user='alice', chat='chat-1', chat_type='dm', platform=Platform.TELEGRAM,
            thread=None, message_id='m-1', name='Alice', chat_name=None, **extra):
    source = SessionSource(platform=platform, chat_id=chat, chat_type=chat_type, user_id=user,
                           user_name=name, thread_id=thread, message_id=message_id, chat_name=chat_name)
    for key, value in extra.items():
        setattr(source, key, value)
    return MessageEvent(text=text, source=source, message_id=message_id)
