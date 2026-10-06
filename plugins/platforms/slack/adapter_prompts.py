"""Native Group Chat choices and related prompt rendering for Slack."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from gateway.platforms.base import SendResult

logger = logging.getLogger(__name__)


class SlackPromptsMixin:
    async def _post_interactive_blocks(
        self, chat_id: str, text: str, blocks: list, metadata: Optional[Dict[str, Any]], *,
        sanitize: bool = True, team_scoped: bool = True):
        """chat.postMessage with ``blocks`` (threaded via metadata); returns the raw response."""
        from plugins.platforms.slack import adapter as facade
        kwargs: Dict[str, Any] = {
            "channel": chat_id, "text": text,
            "blocks": facade.sanitize_blocks(blocks) if sanitize else blocks}
        thread_ts = self._resolve_thread_ts(None, metadata)
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        team_id = self._metadata_team_id(metadata) if team_scoped else None
        return await self._get_client(chat_id, team_id=team_id).chat_postMessage(**kwargs)

    def _group_action_blocks(self, text: str, buttons: list) -> list:
        blocks: list = [{"type": "section", "text": {"type": "plain_text", "text": text[:3000]}}]
        if buttons:
            blocks.append({"type": "actions", "elements": [
                self._button(label, f"hermes_group_{index}", data, style="" if label == "Cancel" else "primary")
                for index, (label, data) in enumerate(buttons)]})
        return blocks

    async def send_group_actions(
        self, chat_id: str, text: str, buttons: list, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        """A Group Chat notice with its choices as Block Kit buttons; each value is
        ``hg:<action>:<token>``, resolved by the gateway when clicked (``gateway.group_chat_actions``),
        so the buttons keep working after a restart."""
        try:
            response = await self._post_interactive_blocks(
                chat_id, text, self._group_action_blocks(text, buttons), metadata)
            return SendResult(success=True, message_id=str((response or {}).get("ts") or ""))
        except Exception as e:  # health: allow BLE001 -- external Slack SDK send errors become an explicit failed SendResult
            logger.warning("[Slack] send_group_actions failed: %s", e)
            return SendResult(success=False, error=str(e))

    async def _handle_group_action(self, ack, body, action) -> None:
        """A click under a Group Chat notice: the gateway rechecks who may choose; the message is
        updated in place into what comes next."""
        started = await self._begin_interaction(ack, body, action, "group")
        if started is None:
            return
        team_id, _action_id, value, _message, msg_ts, channel_id, _user_name, user_id = started
        act = getattr(self.gateway_runner, "_group_chat_action", None)
        try:
            result = await act(self.platform.value, channel_id, user_id, value,
                               scope_id=team_id or None) if act is not None else None
            if result is not None:
                await self._get_client(channel_id, team_id=team_id).chat_update(
                    channel=channel_id, ts=msg_ts, text=result["text"],
                    blocks=self._group_action_blocks(result["text"], result["buttons"]))
        except Exception:  # health: allow BLE001 -- Slack callback delivery crosses the SDK boundary; retain the durable choice and log failed delivery
            logger.warning("[Slack] Group Chat notice choice failed", exc_info=True)
