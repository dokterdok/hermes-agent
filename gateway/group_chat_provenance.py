"""Native/relay author checks retained from #98073; no execution or storage."""
from collections.abc import Mapping
from typing import Any


def is_machine_authored(event: Any) -> bool:
    """Recognize native and relayed bot/webhook provenance defensively."""

    source = getattr(event, "source", None)
    if getattr(source, "is_bot", False):
        return True
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, Mapping) and any(
        metadata.get(key) is True
        for key in ("is_bot", "sender_is_bot", "webhook_sender")
    ):
        return True
    raw = getattr(event, "raw_message", None)
    if isinstance(raw, Mapping):
        if raw.get("bot_id") or raw.get("bot_profile"):
            return True
        if raw.get("subtype") in {"bot_message", "webhook_message"}:
            return True
    for owner_field in ("author", "user", "from_user"):
        owner = getattr(raw, owner_field, None)
        if getattr(owner, "bot", False) or getattr(owner, "is_bot", False):
            return True
    return False



def is_message_edit(event: Any) -> bool:
    """Reject edited commands even when a platform redelivers them as messages."""

    source = getattr(event, "source", None)
    if getattr(source, "message_is_edit", False):
        return True
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, Mapping) and metadata.get("message_is_edit") is True:
        return True
    raw = getattr(event, "raw_message", None)
    if isinstance(raw, Mapping):
        if raw.get("editMessage") or raw.get("isEdited") is True:
            return True
        if raw.get("subtype") == "message_changed":
            return True
        relation = raw.get("m.relates_to")
        if isinstance(relation, Mapping) and relation.get("rel_type") == "m.replace":
            return True
    return bool(getattr(raw, "edit_date", None) or getattr(raw, "edited_at", None))



def relay_provenance_is_unknown(event: Any) -> bool:
    """Fail closed until a relay producer classifies the inbound author."""

    source = getattr(event, "source", None)
    if not getattr(source, "delivered_via_upstream_relay", False):
        return False
    metadata = getattr(event, "metadata", None)
    return not (
        isinstance(metadata, Mapping)
        and metadata.get("relay_author_classified") is True
        and metadata.get("relay_edit_classified") is True
    )
