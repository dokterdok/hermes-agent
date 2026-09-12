"""Preserve accepted Home consent data without restoring live sender proof."""
from dataclasses import replace

from gateway.config import HomeChannel, Platform, PlatformConfig, persist_home_channel
from gateway.group_home_identity import acknowledgement, home_identity
from gateway.session import SessionSource


def test_live_privacy_facts_survive_replace_but_not_session_wire():
    source = SessionSource(Platform.SIGNAL, 'private-chat', user_id='owner',
        is_one_to_one=True, message_is_edit=True, message_had_attachments=True)
    routed = replace(source, profile='worker')
    assert routed.is_one_to_one and routed.message_is_edit and routed.message_had_attachments
    wire = source.to_dict()
    fields = {'is_one_to_one', 'message_is_edit', 'message_had_attachments'}
    assert not fields.intersection(wire)
    restored = SessionSource.from_dict({**wire, **dict.fromkeys(fields, True)})
    assert restored.is_one_to_one is None
    assert not restored.message_is_edit and not restored.message_had_attachments


def test_saved_home_roundtrip_keeps_exact_audience_binding(tmp_path, monkeypatch):
    from hermes_cli.config import load_config
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    home = HomeChannel(Platform.TELEGRAM, '-123', 'Shared home', thread_id='topic',
        user_id='42', selection_id='selection-one')
    home.group_audience_ack = acknowledgement(home)
    persist_home_channel(home)
    raw = load_config()['platforms']['telegram']
    restored = PlatformConfig.from_dict(raw).home_channel
    assert home_identity(restored) == home_identity(home)
    assert restored.group_audience_ack == acknowledgement(restored)
    changed = replace(restored, selection_id='selection-two')
    assert changed.group_audience_ack != acknowledgement(changed)
    assert 'allow_admin_from' not in raw.get('extra', {})


def test_legacy_and_malformed_home_metadata_do_not_gain_consent():
    base = {'platform': 'telegram', 'chat_id': '42', 'name': 'Home'}
    for values in ({}, {'selection_id': True, 'group_audience_ack': 1},
                   {'selection_id': [], 'group_audience_ack': {'accepted': True}}):
        home = HomeChannel.from_dict({**base, **values})
        assert home.selection_id is None and home.group_audience_ack is None
        assert home.to_dict() == base
