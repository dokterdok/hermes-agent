"""Native Group Chat choices and related prompt rendering for Discord."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from gateway.platforms.base import SendResult
from hermes_state_runtime import RuntimeStoreError

logger = logging.getLogger(__name__)


class DiscordPromptsMixin:
    async def send_clarify(
        self, chat_id: str, question: str, choices: Optional[list], clarify_id: str,
        session_key: str, metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Clarify prompt: one button per choice plus ``✏️ Other`` (text-capture); with no choices the
        gateway's text-intercept captures the next message. Dict choices (LLMs emit
        ``[{"description": ...}]``) are unwrapped via ``label``/``description``/``text``/``title``."""
        from plugins.platforms.discord import adapter as facade
        def _flatten_choice(c):
            if c is None:
                return ""
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, dict):
                # 'name'/'value' excluded: Discord-component-shaped fields would leak raw enum values.
                for key in ("label", "description", "text", "title"):
                    v = c.get(key)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                return ""
            if isinstance(c, (list, tuple)):
                return " ".join(_flatten_choice(x) for x in c).strip()
            return str(c).strip()

        def _build(_channel):
            # Header-only card (same rule as the exec approval prompt): the question and hint live
            # in content only, so embed-rendering clients don't see them twice (#114693).
            embed = facade.discord.Embed(title="❓ Hermes needs your input", color=facade.discord.Color.orange())
            # 5 buttons × 5 rows = 25; one slot is reserved for "Other".
            clean_choices = [s for s in (_flatten_choice(c) for c in (choices or [])) if s][:24]
            if clean_choices:
                hint = "Pick one below, or click ✏️ Other to type a custom answer."
                view = facade.ClarifyChoiceView(
                    choices=clean_choices, clarify_id=clarify_id,
                    allowed_user_ids=self._allowed_user_ids,
                    allowed_role_ids=self._allowed_role_ids,
                )
            else:
                hint = "Reply in this channel with your answer."
                view = None
            content = self._self_contained_prompt_content(
                "❓ **Hermes needs your input**", str(question or "").strip(), tail=f"\n\n{hint}",
            )
            send_kwargs = {"content": content, "embed": embed}
            if view:
                send_kwargs["view"] = view
            return send_kwargs, view
        return await self._send_prompt(chat_id, metadata, _build, fail_log="send_clarify")

    async def send_group_actions(
        self, chat_id: str, text: str, buttons: list, metadata: Optional[dict] = None,
    ) -> SendResult:
        """A Group Chat notice with its choices as buttons (one click each, not a select menu). Each
        ``custom_id`` is ``hg:<action>:<token>`` and the view has no timeout; clicks arrive through
        ``_on_group_action_interaction``, which keeps nothing in memory, so they still work after a
        restart (``gateway.group_chat_actions``)."""
        def _build(_channel):
            return {"content": text[:2000], "view": self._group_action_view(buttons)}, None
        return await self._send_prompt(chat_id, metadata, _build, fail_log="send_group_actions")

    @staticmethod
    def _group_action_view(buttons: list):
        from plugins.platforms.discord import adapter as facade
        if not buttons:
            return None
        view = facade.discord.ui.View(timeout=None)
        for label, data in buttons:
            view.add_item(facade.discord.ui.Button(
                label=label, custom_id=data,
                style=facade.discord.ButtonStyle.secondary if label == "Cancel" else facade.discord.ButtonStyle.primary))
        return view

    async def _on_group_action_interaction(self, interaction) -> None:
        """A click on a Group Chat notice button: the gateway rechecks who may choose, and the
        message is edited in place into what comes next."""
        from plugins.platforms.discord import adapter as facade
        data = getattr(interaction, "data", None)
        custom_id = str(data.get("custom_id") or "") if isinstance(data, dict) else ""
        if not custom_id.startswith("hg:"):
            return
        if not facade._component_check_auth(interaction, self._allowed_user_ids, self._allowed_role_ids):
            await interaction.response.send_message(facade._UNAUTHORIZED, ephemeral=True)
            return
        await interaction.response.defer()  # a choice may take longer than Discord's 3 s to answer
        act = getattr(self.gateway_runner, "_group_chat_action", None)
        result = None
        if act is not None:
            try:
                result = await act(self.platform.value, str(interaction.channel_id), str(interaction.user.id),
                                   custom_id)
            except (OSError, RuntimeStoreError):
                logger.warning("[%s] Group Chat notice choice failed", self.name, exc_info=True)
        if result is None:
            await interaction.followup.send(facade._UNAUTHORIZED, ephemeral=True)
            return
        await interaction.edit_original_response(
            content=result["text"][:2000], view=self._group_action_view(result["buttons"]))
