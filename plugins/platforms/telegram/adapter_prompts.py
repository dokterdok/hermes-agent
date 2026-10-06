"""Native Group Chat choices and related prompt rendering for Telegram."""
from __future__ import annotations

import logging
import contextlib
from typing import Any, Dict, Optional

from gateway.platforms.base import SendResult
from hermes_state_runtime import RuntimeStoreError

logger = logging.getLogger(__name__)


class TelegramPromptsMixin:
    async def send_group_actions(
        self, chat_id: str, text: str, buttons: list, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        """A Group Chat notice with its choices as inline buttons. Each button carries its own
        ``hg:<action>:<token>`` (at most 21 bytes), resolved by the gateway when tapped, so it
        keeps working after a restart or another prompt in the chat (``gateway.group_chat_actions``)."""
        from plugins.platforms.telegram import adapter as facade
        def build():
            return facade._html.escape(text), self._group_action_keyboard(buttons), None
        return await self._send_prompt(
            "send_group_actions", chat_id, metadata, build, parse_mode=facade.ParseMode.HTML,
            thread_id=self._metadata_thread_id(metadata))

    @staticmethod
    def _group_action_keyboard(buttons: list):
        from plugins.platforms.telegram import adapter as facade
        return facade.InlineKeyboardMarkup([[facade.InlineKeyboardButton(label, callback_data=data)] for label, data in buttons])\
            if buttons else None

    async def send_clarify(
        self, chat_id: str, question: str, choices: Optional[list], clarify_id: str, session_key: str,
        metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        """Render a clarify prompt: numbered buttons per choice plus "✏️ Other (type answer)" (flips to
        text-capture mode); without choices, plain question and the gateway text-intercept captures."""
        from plugins.platforms.telegram import adapter as facade
        def build():
            text = f"❓ {facade._html.escape(question)}"
            keyboard = None
            if choices:
                # Full option text in the body (mobile truncates button labels); buttons keep numeric labels.
                text += "\n\n" + "\n".join(f"{i + 1}. {facade._html.escape(str(c))}" for i, c in enumerate(choices))
                # Telegram caps callback_data at 64 bytes; keep "cl:<id>:<idx>" short.
                rows = [[facade.InlineKeyboardButton(str(idx + 1), callback_data=f"cl:{clarify_id}:{idx}")] for idx in range(len(choices))]
                rows.append([facade.InlineKeyboardButton("✏️ Other (type answer)", callback_data=f"cl:{clarify_id}:other")])
                keyboard = facade.InlineKeyboardMarkup(rows)
            return text, keyboard, lambda msg: self._clarify_state.__setitem__(clarify_id, session_key)
        return await self._send_prompt(
            "send_clarify", chat_id, metadata, build, parse_mode=facade.ParseMode.HTML, thread_id=self._metadata_thread_id(metadata))

    async def _handle_group_action_callback(self, query, data: str, cb: Dict[str, Any]) -> None:
        """``hg:<action>:<token>`` — a choice under a Group Chat notice. Nothing is kept here: the
        gateway finds the notice by its token, rechecks who may choose, and the message is edited
        in place into what comes next."""
        from plugins.platforms.telegram import adapter as facade
        if not await self._callback_authorized(query, cb, facade._UNAUTHORIZED):
            return
        act = getattr(self.gateway_runner, "_group_chat_action", None)
        result = None
        if act is not None and cb["chat_id"] is not None:
            try:
                result = await act(
                    self.platform.value, str(cb["chat_id"]), str(getattr(query.from_user, "id", "")), data)
            except (OSError, RuntimeStoreError):
                logger.warning("[%s] Group Chat notice choice failed", self.name, exc_info=True)
        if result is None:
            with contextlib.suppress(Exception):
                await query.answer(text=facade._UNAUTHORIZED)
            return
        with contextlib.suppress(Exception):
            await query.edit_message_text(text=facade._html.escape(result["text"]), parse_mode=facade.ParseMode.HTML,
                                          reply_markup=self._group_action_keyboard(result["buttons"]))
        with contextlib.suppress(Exception):  # a slow choice may outlive the tap's answer window
            await query.answer()
