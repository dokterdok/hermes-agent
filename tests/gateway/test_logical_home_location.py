"""Home identity ignores only Slack's session-only top-level thread."""
from dataclasses import replace

from gateway.config import Platform
from gateway.group_home_identity import home_thread_from_source, logical_home_source
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


def test_explicit_native_thread_root_is_not_collapsed_to_the_channel():
    source = SessionSource(Platform.SLACK, 'channel', thread_id='123.456', message_id='123.456')
    event = MessageEvent(source=source, text='hello', message_id='123.456', raw_message={'ts': '123.456', 'thread_ts': '123.456'})
    logical = logical_home_source(event)
    assert home_thread_from_source(logical) == '123.456'
    assert event.source is source and source.message_id == '123.456'
    top_level = replace(event, raw_message={'ts': '123.456'})
    assert logical_home_source(top_level).thread_id is None
    assert source.thread_id == '123.456'
