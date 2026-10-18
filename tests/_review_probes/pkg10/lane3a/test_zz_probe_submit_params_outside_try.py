"""Probe B: CLI-36 moved ``_submit_params`` out of run_conversation's ``try``. ``_submit_params`` stages
image parts with ``image.attach_bytes`` (a gateway call), so a lost connection or an owner refusal while
staging an image now escapes run_conversation as an exception instead of main's failed-turn result
(``failure_reason='gateway_connection'`` / the refusal text)."""
from tests._review_probes.pkg10.lane3a._cli_harness import RecordingConnection, _agent


def test_image_staging_failure_is_a_failed_turn_not_an_exception():
    conn = RecordingConnection({"model": "old/model", "provider": "openrouter"})  # attach_bytes -> GatewayClientError
    agent = _agent(conn, model="old/model", provider="openrouter")
    parts = [{"type": "text", "text": "what is this"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]
    result = agent.run_conversation(parts)
    assert result.get("failed") is True, result
    assert result.get("failure_reason") == "gateway_connection", result
    assert not [m for m, _ in conn.calls if m == "prompt.submit"], conn.calls
