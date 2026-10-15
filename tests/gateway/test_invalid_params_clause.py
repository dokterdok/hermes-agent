"""Named ``invalid_params`` refusals.

The reason code stays the bare token callers and the wire compare. The clause
name is a separate, non-secret field: logged, carried on the RPC error, and
shown to the person who was refused.
"""
import base64
import logging

import pytest

from hermes_state_runtime import RuntimeStoreError


def test_bare_reason_code_is_unchanged():
    """Control: a refusal with no clause still stringifies as the code alone."""
    exc = RuntimeStoreError("invalid_params")
    assert exc.reason == "invalid_params"
    assert str(exc) == "invalid_params"


def test_named_clause_does_not_change_the_reason_code():
    exc = RuntimeStoreError("invalid_params", detail="toolsets")
    assert exc.reason == "invalid_params"
    assert str(exc) == "invalid_params"
    assert exc.detail == "toolsets"


def test_refusal_logger_routes_to_gateway_log():
    """Control: the refusal logger is a gateway component, so the line lands in gateway.log."""
    from hermes_logging import COMPONENT_PREFIXES
    assert any("gateway.runtime".startswith(prefix) for prefix in COMPONENT_PREFIXES["gateway"])


def test_missing_staging_file_names_the_staging_clause(tmp_path, monkeypatch, caplog):
    from gateway.platforms import base
    from gateway.session_ingress_media import admit_attachments

    staging = tmp_path / "images"
    staging.mkdir()
    monkeypatch.setattr(base, "get_image_cache_dir", lambda: staging)
    missing = staging / "gone.png"
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            admit_attachments([{"path": str(missing), "mime": "image/png"}])
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "attachments.staging"
    assert "invalid_params: attachments.staging" in caplog.text
    assert str(missing) not in caplog.text


def test_bmp_mime_names_the_mime_clause(caplog):
    from gateway.session_ingress_media import admit_attachments

    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            admit_attachments([{"path": "/tmp/scan.bmp", "mime": "image/bmp"}])
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "attachments.mime"
    assert "invalid_params: attachments.mime" in caplog.text
    assert "image/bmp" not in caplog.text


def test_chat_upload_rejects_bmp_outside_the_admission_set():
    """Upload allows only the mime set admission will keep. A BMP that got
    through here used to die later as a bare ``invalid_params``."""
    from fastapi import HTTPException

    from hermes_cli.web_models import ChatImageUpload
    from hermes_cli.web_routers.files import _decode_chat_image_upload

    payload = ChatImageUpload(data_url="data:image/bmp;base64," + base64.b64encode(b"BM" + b"\x00" * 16).decode())
    with pytest.raises(HTTPException) as caught:
        _decode_chat_image_upload(payload)
    assert caught.value.status_code == 400


def test_chat_upload_still_accepts_png():
    """Control: a mime admission accepts is still a valid clipboard upload."""
    from hermes_cli.web_models import ChatImageUpload
    from hermes_cli.web_routers.files import _decode_chat_image_upload

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    payload = ChatImageUpload(data_url="data:image/png;base64," + base64.b64encode(png).decode())
    data, mime, ext = _decode_chat_image_upload(payload)
    assert data.startswith(b"\x89PNG") and mime == "image/png" and ext == ".png"


@pytest.mark.parametrize("extra,detail", [
    ({"nope": 1}, "nope"),
    ({"source": "cron"}, "source"),
    ({"safe_mode": "yes"}, "safe_mode"),
    ({"api_key": "  "}, "api_key"),
    ({"model": ""}, "model"),
    ({"cwd": "."}, "cwd"),
    ({"row_cwd": "rel"}, "row_cwd"),
    ({"provider": " "}, "provider"),
    ({"base_url": ""}, "base_url"),
    ({"base_url": "http://user:secret@127.0.0.1/v1"}, "base_url"),
    ({"yolo": 1}, "yolo"),
    ({"ignore_rules": "false"}, "ignore_rules"),
    ({"service_tier": "warp"}, "service_tier"),
    ({"run_budget": 0}, "run_budget"),
    ({"max_turns": "garbage"}, "max_turns"),
    ({"reasoning": "nope"}, "reasoning"),
    ({"toolsets": ["not-a-toolset"]}, "toolsets"),
    ({"toolsets": ["desktop_ui"], "source": "cli"}, "toolsets"),
    ({"skills": []}, "skills"),
    ({"skills": ["launch-fixture"], "safe_mode": True}, "skills"),
])
def test_session_policy_names_the_failing_clause(tmp_path, caplog, extra, detail):
    from gateway.session_policy import build_policy

    params = {"cwd": str(tmp_path)}
    params.update(extra)
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            build_policy(params, {})
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == detail
    assert f"invalid_params: {detail}" in caplog.text
    assert "secret" not in caplog.text


def test_toolset_outside_the_enabled_set_names_toolsets(tmp_path, caplog):
    """A valid name the profile has disabled still refuses, and names ``toolsets``
    (the subset check, distinct from an unknown name)."""
    from gateway.session_policy import build_policy

    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            build_policy(
                {"cwd": str(tmp_path), "toolsets": ["web", "terminal"]},
                {"agent": {"disabled_toolsets": ["web"]}},
            )
    assert caught.value.detail == "toolsets"
    assert str(caught.value) == "invalid_params"
    assert "invalid_params: toolsets" in caplog.text


def test_blank_rebind_key_names_api_key_not_the_value(caplog):
    from types import SimpleNamespace

    from gateway.session_policy import rebind_launch_key

    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            rebind_launch_key(SimpleNamespace(), SimpleNamespace(session_id="s"), "   ")
    assert caught.value.detail == "api_key"
    assert str(caught.value) == "invalid_params"
    assert "invalid_params: api_key" in caplog.text


def _native_event():
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.session import SessionSource

    source = SessionSource(platform=Platform.TELEGRAM, chat_id="chat", user_id="user")
    return MessageEvent(text="hello", source=source)


def _runner(*, multiplex):
    from types import SimpleNamespace

    runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=multiplex))
    runner._is_user_authorized_for_source = lambda source, allow_adapter_delegation=False: True
    runner._adapter_for_source = lambda source: object()
    return runner


def test_multiplex_missing_provenance_names_its_clause(caplog):
    from gateway.session_envelope import _validate_native

    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            _validate_native(_runner(multiplex=True), _native_event(), provenance=None)
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "native.provenance_missing(multiplex)"
    assert "invalid_params: native.provenance_missing(multiplex)" in caplog.text


def test_media_urls_none_names_its_clause(caplog):
    from gateway.session_envelope import _validate_native

    event = _native_event()
    event.media_urls = None
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            _validate_native(_runner(multiplex=False), event, provenance=None)
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "native.media_urls"
    assert "invalid_params: native.media_urls" in caplog.text


@pytest.mark.asyncio
async def test_session_create_rpc_keeps_the_code_and_names_toolsets(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    import hermes_state_runtime as rt

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(json.dumps({"model": {"default": "m"}}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(run, "_hermes_home", home)
    store = SessionStore(home / "sessions", GatewayConfig())
    db = store._db
    runner = SimpleNamespace(
        session_store=store, _session_db=db, adapters={}, _draining=False,
        _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
        _handle_message=None, _peek_session_state=lambda key: None,
    )
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(
        runner, profile_id="owned", instance_id="owner", db=db,
        epoch=rt.begin_runtime_epoch(db, instance_id="owner"),
    )
    owner = AuthorityConnection(authority, object(), {"user_id": "human"})
    try:
        response = await owner.dispatch({"id": "bad-toolset", "method": "session.create", "params": {
            "request_id": "bad-toolset", "source": "cli", "cwd": str(tmp_path),
            "toolsets": ["not-a-toolset"], "model": "m",
        }})
    finally:
        await owner.close()
        store.close_all_db_handles()
    error = response["error"]
    assert error["message"] == "invalid_params"
    assert error["data"]["reason"] == "invalid_params"
    assert error["data"]["detail"] == "toolsets"


def test_cli_error_line_names_toolsets(monkeypatch, capsys):
    import argparse

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display
    import hermes_cli.gateway_chat as chat
    import hermes_cli.gateway_chat_startup as startup
    from hermes_cli.cli_chat_error_copy import agent_init_failure_message

    exc = GatewayRPCError("invalid_params", 4001, {"reason": "invalid_params", "detail": "toolsets"})
    assert str(exc) == "invalid_params"
    assert refusal_display(exc) == "invalid_params (toolsets)"
    assert agent_init_failure_message(exc) == "Error: invalid_params (toolsets)"

    async def refused(args, emitter=None):
        raise exc

    monkeypatch.setattr(chat, "validate_options", lambda args: None)
    monkeypatch.setattr(chat, "_register_terminal_process", lambda: None)
    monkeypatch.setattr(startup, "ensure_launch_provider", lambda args: True)
    monkeypatch.setattr(chat, "run_gateway_chat", refused)
    args = argparse.Namespace(
        output_format="text", list_tools=False, list_toolsets=False,
        query="hi", q=None, oneshot=None, query_file=None,
    )
    assert chat.launch_from_args(args) == 1
    assert capsys.readouterr().err.strip() == "Error: invalid_params (toolsets)"


def test_messaging_refusal_names_the_clause_not_the_class():
    from agent.i18n import t
    from hermes_state_runtime import named_turn_error

    exc = RuntimeStoreError("invalid_params", detail="native.provenance_missing(multiplex)")
    error_type, error_detail = named_turn_error(exc, "no details available")
    text = t("gateway.notify.turn_error_detailed", error_type=error_type, error_detail=error_detail)
    assert "RuntimeStoreError" not in text
    assert "native.provenance_missing(multiplex)" in text
    assert "invalid_params" in text


@pytest.mark.asyncio
async def test_notify_turn_error_sends_the_named_refusal():
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.event import MessageEvent
    from gateway.session import SessionSource

    class _Adapter(BasePlatformAdapter):
        def __init__(self):
            super().__init__(PlatformConfig(enabled=True, typing_indicator=False), Platform.TELEGRAM)
            self.sent = []

        async def connect(self, *, is_reconnect=False):
            return True

        async def disconnect(self):
            pass

        async def get_chat_info(self, chat_id):
            return {"id": chat_id}

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.sent.append(content)
            from gateway.platforms.base import SendResult
            return SendResult(success=True, message_id="1")

    adapter = _Adapter()
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="42", user_id="42")
    event = MessageEvent(text="hi", source=source)
    await adapter._notify_turn_error(
        event, RuntimeStoreError("invalid_params", detail="native.media_urls"))
    assert adapter.sent
    body = adapter.sent[0]
    assert "RuntimeStoreError" not in body
    assert "native.media_urls" in body


def _set_event(**fields):
    return lambda event: [setattr(event, name, value) for name, value in fields.items()]


def _set_source(**fields):
    return lambda event: [setattr(event.source, name, value) for name, value in fields.items()]


# Every other clause _validate_native refuses with. The multiplex provenance
# and media_urls=None clauses have their own tests above.
_NATIVE_REFUSALS = {
    "native.source": _set_event(source=None),
    "native.text": _set_event(text=None),
    "native.message_type": _set_event(message_type="text"),
    "native.command": _set_event(text="/new"),
    "native.internal": _set_event(internal=True),
    "native.metadata": _set_event(metadata={"other": 1}),
    "native.prompt_response": _set_event(prompt_response={"id": "p"}),
    "native.stale_roles": _set_source(role_authorized=True),
    "native.relay_provenance": _set_source(delivered_via_upstream_relay=True),
    "native.profile_route_rejected": _set_source(profile_route_rejected=True),
    "native.provenance_missing(authorization_home)": _set_source(_authorization_profile_home="/tmp/home"),
    "native.channel_prompt": _set_event(channel_prompt=1),
    "native.channel_context": _set_event(channel_context=1),
    "native.reply_expected": _set_event(reply_expected="yes"),
    "native.auto_skill": _set_event(auto_skill=[1]),
    "native.media_types": _set_event(media_types=None),
    "native.media_text_inlined": _set_event(media_text_inlined=["x"]),
    "native.media_length": _set_event(media_urls=["/a.png", "/b.png"], media_types=["image/png"]),
}


@pytest.mark.parametrize("clause", sorted(_NATIVE_REFUSALS))
def test_native_refusal_names_its_clause(caplog, clause):
    from gateway.session_envelope import _validate_native

    event = _native_event()
    _NATIVE_REFUSALS[clause](event)
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            _validate_native(_runner(multiplex=False), event, provenance=None)
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == clause
    assert [rec.getMessage() for rec in caplog.records] == [f"invalid_params: {clause}"]


def test_schema_extra_key_is_named_logged_and_shown(tmp_path, monkeypatch, caplog):
    """An unknown session.create key is refused by the canonical schema, before build_policy."""
    import asyncio

    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {"nope": 1}))
    error = response["error"]
    assert error["message"] == "invalid_params"
    assert error["data"]["reason"] == "invalid_params"
    assert error["data"]["fields"] == ["nope"]
    assert error["data"]["detail"] == "nope"
    assert logs == ["invalid_params: nope"]
    from hermes_cli.gateway_client import GatewayRPCError, refusal_display
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    assert shown == "invalid_params (nope)"


def test_schema_bad_type_names_the_field(tmp_path, monkeypatch, caplog):
    import asyncio

    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {"toolsets": "terminal", "source": "cron"}))
    error = response["error"]
    assert error["message"] == "invalid_params"
    assert "toolsets" in error["data"]["fields"] and "source" in error["data"]["fields"]
    assert error["data"]["detail"]
    assert "terminal" not in error["data"]["detail"]
    assert logs and all(not line.endswith("terminal") for line in logs)
    from hermes_cli.gateway_client import GatewayRPCError, refusal_display
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    assert shown.startswith("invalid_params (")
    assert "toolsets" in shown and "source" in shown


def test_token_shaped_key_is_not_echoed(tmp_path, monkeypatch, caplog):
    import asyncio

    secret = "sk_live_" + "Ab12Cd34" * 3
    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {secret: 1}))
    detail = response["error"]["data"]["detail"]
    assert response["error"]["message"] == "invalid_params"
    assert secret not in detail
    assert secret not in " ".join(logs)
    assert secret not in " ".join(response["error"]["data"]["fields"])
    assert detail == "<redacted-key>"
    assert logs == ["invalid_params: <redacted-key>"]
    from hermes_cli.gateway_client import GatewayRPCError, refusal_display
    # A frame that only has the raw fields list (no detail) must still not print the secret.
    bare = GatewayRPCError("invalid_params", 4001, {"reason": "invalid_params", "fields": [secret]})
    shown = refusal_display(bare)
    assert secret not in shown
    assert shown == "invalid_params (<redacted-key>)"


def test_fields_only_frame_names_the_field():
    from hermes_cli.gateway_client import GatewayRPCError, refusal_display
    exc = GatewayRPCError("invalid_params", 4001, {"reason": "invalid_params", "fields": ["nope"]})
    assert str(exc) == "invalid_params"
    assert refusal_display(exc) == "invalid_params (nope)"


def test_underscore_extra_key_logs_a_safe_clause(tmp_path, caplog):
    from gateway.session_policy import build_policy

    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            build_policy({"cwd": str(tmp_path), "_private": 1}, {})
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "_private"
    assert "invalid_params: _private" in caplog.text


def test_hardlink_staging_logs_the_clause_once(tmp_path, monkeypatch, caplog):
    import os

    from gateway.platforms import base
    from gateway.session_ingress_media import admit_attachments

    staging = tmp_path / "images"
    staging.mkdir()
    monkeypatch.setattr(base, "get_image_cache_dir", lambda: staging)
    original = staging / "one.png"
    alias = staging / "two.png"
    original.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    os.link(original, alias)
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            admit_attachments([{"path": str(alias), "mime": "image/png"}])
    assert caught.value.detail == "attachments.staging"
    assert str(caught.value) == "invalid_params"
    assert [rec.getMessage() for rec in caplog.records] == ["invalid_params: attachments.staging"]


@pytest.mark.asyncio
async def test_role_gated_missing_provenance_names_the_clause(caplog):
    from gateway.session_envelope import prepare_native
    from hermes_state_runtime import named_turn_error

    event = _native_event()
    event.source.role_authorized = True
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            await prepare_native(_runner(multiplex=True), event)
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "native.provenance_missing(roles)"
    assert "invalid_params: native.provenance_missing(roles)" in caplog.text
    error_type, error_detail = named_turn_error(caught.value, "no details available")
    assert error_type == "invalid_params"
    assert "RuntimeStoreError" not in error_type
    assert error_detail == "native.provenance_missing(roles)"


@pytest.mark.asyncio
async def test_role_gated_relay_without_relay_provenance_names_the_clause(caplog):
    from gateway.session_ingress_context import reauthorize_roles

    source = _native_event().source
    source.role_authorized = True
    source.delivered_via_upstream_relay = True
    with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
        with pytest.raises(RuntimeStoreError) as caught:
            await reauthorize_roles(_runner(multiplex=False), source, {"transport_home": "/tmp"})
    assert caught.value.reason == "invalid_params"
    assert str(caught.value) == "invalid_params"
    assert caught.value.detail == "native.relay_provenance"
    assert "invalid_params: native.relay_provenance" in caplog.text


@pytest.mark.parametrize("secret, clause", [
    ("ghp_AAAAAAAA.BBBBBBBB", "<redacted-key>"),
    ("sk-sp-" + "AbCdEf12." + "GhIjKl34." + "MnOpQr56", "<redacted-key>"),
    ("ghs_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6", "<redacted-key>"),
    # The log redactor's Slack pattern is xox[baprs]-, so xoxc- is not a hit.
    # The key is not an identifier either: the clause is the method fallback
    # and the raw token is still absent from detail, fields, the log, and the CLI.
    ("xoxc-" + "123456789012-1234567890123-" + "AbCdEfGhIjKlMnOpQrStUv", "create.fields"),
])
def test_credential_shaped_key_is_not_copied_into_detail_log_or_cli(
        tmp_path, monkeypatch, caplog, secret, clause):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {secret: 1}))
    error = response["error"]
    detail = error["data"]["detail"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    blob = " ".join([detail, shown, *logs, *error["data"]["fields"]])
    assert error["message"] == "invalid_params"
    assert secret not in blob
    assert "AbCdEf" not in blob
    assert detail == clause
    assert logs == [f"invalid_params: {clause}"]
    assert shown == f"invalid_params ({clause})"


def _assert_secret_is_redacted(response, logs):
    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    blob = " ".join([error["data"]["detail"], shown, *logs, *map(str, error["data"]["fields"])])
    assert error["message"] == "invalid_params"
    assert "AbCdEf" not in blob
    assert "Ab12Cd34" not in blob
    assert error["data"]["detail"] == "<redacted-key>"
    assert logs == ["invalid_params: <redacted-key>"]
    assert shown == "invalid_params (<redacted-key>)"
    return blob


@pytest.mark.parametrize("secret", [
    "__hf_" + ("Ab12Cd34" * 4) + "Ab",
    "___hf_" + ("Ab12Cd34" * 4),
    "__sk_live_" + "AbCdEf1234" + "AbCdEf1234" + "AbCd",
    "___sk_live_" + "AbCdEf1234" + "AbCdEf1234",
    "__am_" + "AbCdEfGhIjKlMnOpQrSt",
    "___am_" + "AbCdEfGhIjKlMnOpQrSt",
    "__SG." + "AbCdEfGhIjKlMnOpQrStUv",
    "___SG." + "AbCdEfGhIjKlMnOpQrStUv",
    "__npm_" + "AbCdEf12." + "GhIjKl34." + "MnOpQr56",
    # An underscore that is not the first character still hides the prefix.
    "foo__hf_" + ("Ab12Cd34" * 3),
    "bar__am_" + "AbCdEfGhIjKlMnOpQrSt",
    "x__SG." + "AbCdEfGhIjKlMnOpQrStUv",
])
def test_extra_leading_underscores_are_not_echoed(tmp_path, monkeypatch, caplog, secret):
    import asyncio

    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {secret: 1}))
    blob = _assert_secret_is_redacted(response, logs)
    assert secret not in blob


@pytest.mark.parametrize("secret", [
    "__hf_" + ("Ab12Cd34" * 4) + "Ab",
    "___hf_" + ("Ab12Cd34" * 3),
    "__am_" + "AbCdEfGhIjKlMnOpQrSt",
    "__SG." + "AbCdEfGhIjKlMnOpQrStUv",
    "__npm_" + "AbCdEf12." + "GhIjKl34." + "MnOpQr56",
    "foo__hf_" + ("Ab12Cd34" * 3),
    "x__SG." + "AbCdEfGhIjKlMnOpQrStUv",
])
def test_nested_leading_underscores_are_not_echoed(tmp_path, monkeypatch, caplog, secret):
    import asyncio

    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {},
        method="prompt.submit",
        params={"session_id": "s", "text": "hi", "turn_author": {secret: "x"}},
    ))
    blob = _assert_secret_is_redacted(response, logs)
    assert secret not in blob


# The redactor's lookahead treats ``_`` as part of a token too, so an
# underscore after the body hides it the same way one in front does.
_TRAILING_UNDERSCORE_TOKENS = [
    ("hf_" + "Ab12Cd34" * 4, "_x"),
    ("ghp_" + "AbCdEf1234" * 3 + "Ab", "_"),
    ("npm_" + "AbCdEf1234" * 2, "_x"),
    ("am_" + "AbCdEfGhIjKlMnOpQrSt", "_"),
    ("AKIA" + "IOSFODNN7EXAMPLE", "_x"),
    ("npm_" + "AbCdEf12." + "GhIjKl34", "_x"),
]


def _assert_token_not_published(response, logs, token):
    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    blob = " ".join([error["data"]["detail"], shown, *logs, *map(str, error["data"]["fields"])])
    assert error["message"] == "invalid_params"
    assert token not in blob
    assert token.replace(".", "") not in blob
    assert error["data"]["detail"] == "<redacted-key>"
    assert logs == ["invalid_params: <redacted-key>"]
    assert shown == "invalid_params (<redacted-key>)"


@pytest.mark.parametrize("token, tail", _TRAILING_UNDERSCORE_TOKENS)
def test_trailing_underscore_does_not_hide_a_token(tmp_path, monkeypatch, caplog, token, tail):
    import asyncio

    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {token + tail: 1}))
    _assert_token_not_published(response, logs, token)


@pytest.mark.parametrize("token, tail", _TRAILING_UNDERSCORE_TOKENS)
def test_nested_trailing_underscore_does_not_hide_a_token(tmp_path, monkeypatch, caplog, token, tail):
    import asyncio

    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {},
        method="prompt.submit",
        params={"session_id": "s", "text": "hi", "turn_author": {"x_" + token + tail: "x"}},
    ))
    _assert_token_not_published(response, logs, token)


def test_overlong_key_is_not_probed_run_by_run(tmp_path, monkeypatch, caplog):
    """A key too long to publish gets one redactor pass, not one per run of it.
    The run-by-run probe is quadratic and held the event loop for seconds on a
    few thousand characters."""
    import asyncio

    from agent import redact

    key = "a_" * 2000
    real = redact.redact_sensitive_text
    probes = []

    def counting(text, **kwargs):
        if "a_a_" in text:
            probes.append(len(text))
        return real(text, **kwargs)

    monkeypatch.setattr(redact, "redact_sensitive_text", counting)
    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {key: 1}))
    error = response["error"]
    assert error["message"] == "invalid_params"
    assert error["data"]["detail"] == "create.fields"
    assert key not in " ".join([*logs, *error["data"]["fields"]])
    assert len(probes) < 20


def test_many_extra_keys_stay_within_the_client_bound(tmp_path, monkeypatch, caplog):
    """The published list must survive rpc_error's data bound, and keys past
    it are never probed. Each probe is a redactor pass per run of the name."""
    import asyncio

    from agent import redact
    from hermes_cli.gateway_client import refusal_display, rpc_error

    keys = [f"extra{index:03d}" for index in range(200)]
    real = redact.redact_sensitive_text
    probed = set()

    def counting(text, **kwargs):
        probed.update(key for key in keys if key in text)
        return real(text, **kwargs)

    monkeypatch.setattr(redact, "redact_sensitive_text", counting)
    response, _logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, dict.fromkeys(keys, 1)))
    error = response["error"]
    fields = error["data"]["fields"]
    assert error["message"] == "invalid_params"
    assert fields[0] == keys[0] and fields[-1] == "create.fields"
    received = rpc_error(error)
    assert received.data["fields"] == fields
    assert refusal_display(received).startswith(f"invalid_params ({keys[0]}")
    assert keys[-1] not in probed
    assert len(probed) < len(keys) // 4


@pytest.mark.parametrize("name", ["__private", "___private", "__am_example_identifier_123"])
def test_extra_underscores_on_a_plain_name_stay_echoed(tmp_path, monkeypatch, caplog, name):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {name: 1}))
    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    assert error["message"] == "invalid_params"
    assert error["data"]["detail"] == name
    assert name in shown
    assert logs == [f"invalid_params: {name}"]


@pytest.mark.parametrize("secret", [
    "npm_" + "AbCdEf12." + "GhIjKl34." + "MnOpQr56",
    "rk_live_" + "AbCdEf1234",
    "hf_" + "AbCdEf123456." + "GhIjKl789012",
    "am_" + "AbCdEfGhIjKlMnOpQrSt",
    "_ghp_" + "AbCdEf1234",
])
def test_redactor_known_shapes_are_not_echoed(tmp_path, monkeypatch, caplog, secret):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    response, logs = asyncio.run(_dispatch_create(tmp_path, monkeypatch, caplog, {secret: 1}))
    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    blob = " ".join([error["data"]["detail"], shown, *logs, *error["data"]["fields"]])
    assert error["message"] == "invalid_params"
    assert secret not in blob
    assert "AbCdEf" not in blob
    assert error["data"]["detail"] == "<redacted-key>"
    assert logs == ["invalid_params: <redacted-key>"]
    assert shown == "invalid_params (<redacted-key>)"


def test_list_index_locs_stay_named_beside_safe_fields(tmp_path, monkeypatch, caplog):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {},
        params={"request_id": "schema", "cwd": str(tmp_path), "model": "m",
                "toolsets": [1], "source": "cron"},
    ))
    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    detail = error["data"]["detail"]
    blob = " ".join([detail, shown, *logs, *error["data"]["fields"]])
    assert "toolsets.0" in detail and "source" in detail
    assert "toolsets.0" in shown and "source" in shown
    assert logs and "toolsets.0" in logs[0] and "source" in logs[0]
    assert "create.fields" not in blob
    assert "cron" not in detail
    assert "cron" not in " ".join(logs)


def test_secret_loc_does_not_hide_a_safe_sibling(tmp_path, monkeypatch, caplog):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    secret = "rk_live_" + "AbCdEf1234"
    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {secret: 1},
        params={"request_id": "schema", "cwd": str(tmp_path), "model": "m",
                "source": "cron", secret: 1},
    ))
    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    detail = error["data"]["detail"]
    blob = " ".join([detail, shown, *logs, *error["data"]["fields"]])
    assert "source" in detail and "<redacted-key>" in detail
    assert "source" in shown and "<redacted-key>" in shown
    assert logs and "source" in logs[0] and "<redacted-key>" in logs[0]
    assert secret not in blob and "AbCdEf" not in blob
    assert "cron" not in detail


@pytest.mark.parametrize("attachments, needle", [
    ([{"path": 1, "mime": "image/png"}], "attachments.0.path"),
    ([{"path": "/tmp/a.png", "mime": "image/png", "bogus": True}], "attachments.0.bogus"),
])
def test_attachment_index_locs_name_the_field(tmp_path, monkeypatch, caplog, attachments, needle):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {},
        method="prompt.submit",
        params={"session_id": "s", "text": "hi", "attachments": attachments},
    ))
    error = response["error"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    blob = " ".join([error["data"]["detail"], shown, *logs, *error["data"]["fields"]])
    assert needle in error["data"]["detail"]
    assert needle in shown
    assert logs and needle in logs[0]
    assert "submit.fields" not in blob


def test_nested_token_path_is_not_echoed(tmp_path, monkeypatch, caplog):
    import asyncio

    from hermes_cli.gateway_client import GatewayRPCError, refusal_display

    secret = "sk_live_" + ("Ab12Cd34" * 3)
    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {},
        method="prompt.submit",
        params={"session_id": "s", "text": "hi", "turn_author": {secret: "x"}},
    ))
    error = response["error"]
    detail = error["data"]["detail"]
    shown = refusal_display(GatewayRPCError(error["message"], error["code"], error["data"]))
    blob = " ".join([detail, shown, *logs, *map(str, error["data"]["fields"])])
    assert secret not in blob
    assert "Ab12Cd34" not in blob
    assert detail == "<redacted-key>"


def test_resume_fallback_is_not_create_fields(tmp_path, monkeypatch, caplog):
    import asyncio

    response, logs = asyncio.run(_dispatch_create(
        tmp_path, monkeypatch, caplog, {},
        method="session.resume",
        params={"session_id": "s", "_private": 1, "user:s3cret-pass": 1},
    ))
    detail = response["error"]["data"]["detail"]
    blob = " ".join([detail, *logs, *response["error"]["data"]["fields"]])
    assert "create.fields" not in blob
    assert "s3cret" not in blob
    assert "_private" in detail or detail == "resume.fields"
    assert "resume.fields" in detail or "_private" in detail


def test_exit_code_uses_the_reason_not_the_rendered_clause(monkeypatch, capsys):
    import argparse

    from hermes_cli.gateway_client import GatewayClientError, GatewayRPCError
    import hermes_cli.gateway_chat as chat
    import hermes_cli.gateway_chat_startup as startup

    exc = GatewayRPCError("invalid_params", 4001, {
        "reason": "invalid_params", "detail": "unsupported_flag"})

    async def refused(args, emitter=None):
        raise exc

    monkeypatch.setattr(chat, "validate_options", lambda args: None)
    monkeypatch.setattr(chat, "_register_terminal_process", lambda: None)
    monkeypatch.setattr(startup, "ensure_launch_provider", lambda args: True)
    monkeypatch.setattr(chat, "run_gateway_chat", refused)
    args = argparse.Namespace(
        output_format="text", list_tools=False, list_toolsets=False,
        query="hi", q=None, oneshot=None, query_file=None,
    )
    assert chat.launch_from_args(args) == 1
    err = capsys.readouterr().err
    assert "unsupported_flag" in err
    usage = GatewayClientError("Unsupported gateway CLI options: --image")
    assert chat.client_error_exit_code(usage) == 2
    assert chat.client_error_exit_code(exc) == 1


def _dispatch_create(tmp_path, monkeypatch, caplog, extra, *, method="session.create", params=None):
    import json
    from types import SimpleNamespace

    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    import hermes_state_runtime as rt

    async def go():
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text(json.dumps({"model": {"default": "m"}}))
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(run, "_hermes_home", home)
        store = SessionStore(home / "sessions", GatewayConfig())
        db = store._db
        runner = SimpleNamespace(
            session_store=store, _session_db=db, adapters={}, _draining=False,
            _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
            _handle_message=None, _peek_session_state=lambda key: None,
        )
        runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
        authority = SessionAuthority(
            runner, profile_id="owned", instance_id="owner", db=db,
            epoch=rt.begin_runtime_epoch(db, instance_id="owner"),
        )
        owner = AuthorityConnection(authority, object(), {"user_id": "human"})
        try:
            body = params if params is not None else {
                "request_id": "schema", "source": "cli", "cwd": str(tmp_path), "model": "m", **extra,
            }
            with caplog.at_level(logging.WARNING, logger="gateway.runtime"):
                response = await owner.dispatch({"id": "schema", "method": method, "params": body})
            logs = [rec.getMessage() for rec in caplog.records if rec.name == "gateway.runtime"]
            return response, logs
        finally:
            await owner.close()
            store.close_all_db_handles()

    return go()
