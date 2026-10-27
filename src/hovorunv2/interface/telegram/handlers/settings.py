"""Unified settings command with hierarchical paginated menus."""

import contextlib
import math
from typing import Any, ClassVar, Final

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.data.languages import LANGUAGES, POPULAR_LANGUAGES
from hovorunv2.application.features import FeatureChannel
from hovorunv2.application.permissions import DecisionReason
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.application.services.settings_session_service import (
    SETTINGS_SESSION_TTL,
    SettingsContext,
    SettingsSessionService,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import (
    CommandName,
)
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import SettingsAction, SettingsCallback, SettingsMenu

from .base import BaseCommand

logger = get_logger(__name__)

CONFIG_WINDOW_TTL: Final = SETTINGS_SESSION_TTL
LANGS_PER_PAGE: Final = 10


class SettingsCommand(BaseCommand):
    """Unified settings command replacing legacy configuration commands."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_group_admin=True,
        requires_approval=True,
        is_toggleable=False,
    )

    def __init__(
        self,
        language_service: LanguageService,
        command_service: CommandService,
        access_service: AccessService,
        cleanup_service: CleanupService,
        sessions: SettingsSessionService,
    ) -> None:
        """Initialize command."""
        self._language_service = language_service
        self._command_service = command_service
        self._access_service = access_service
        self._cleanup_service = cleanup_service
        self._sessions = sessions

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.SETTINGS

    @property
    def trigger(self) -> Command:
        """Trigger filter for settings command."""
        return Command(CommandName.SETTINGS)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /settings."""
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts:
            return False
        cmd_part = parts[0].lstrip("/").split("@")[0].lower()
        return cmd_part == CommandName.SETTINGS

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle /settings command."""
        if message.sender_chat or not message.from_user or message.from_user.is_bot:
            return
        try:
            context = await self._sessions.prepare(
                message.chat.id, message.from_user.id, message.chat.id, message.message_id
            )
        except ValueError:
            return
        if not await self._authorized(message.from_user.id, context, bot):
            return
        msg = await message.answer("⚙️ <b>Hovorun Settings</b>", parse_mode="HTML")
        context = context.model_copy(update={"message_id": msg.message_id})
        await self._cleanup_service.replace_singleton_window(
            bot, msg.chat.id, msg.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
        )
        await self._show_main_menu(msg, context)

    async def _authorized(self, actor_id: int, context: SettingsContext, bot: Bot) -> bool:
        """Recheck current actor restrictions and target-specific Telegram/bot authority."""
        if await self._access_service.is_banned(actor_id):
            return False
        if context.target_chat_id > 0:
            return context.target_chat_id == actor_id and await self._access_service.is_admin(actor_id)
        return await self._access_service.is_allowed(actor_id, context.target_chat_id, self.POLICY, bot)

    async def _render(
        self, message: Message, text: str, builder: InlineKeyboardBuilder, context: SettingsContext
    ) -> None:
        """Bind every action to this exact window and invalidate the previous render."""
        callbacks = [
            SettingsCallback.unpack(button.callback_data)
            for row in builder.export()
            for button in row
            if button.callback_data and button.callback_data.startswith("settings:")
        ]
        try:
            token = await self._sessions.issue(context, [callback.pack() for callback in callbacks])
        except ValueError:
            await message.edit_text("Settings expired. Reopen /settings.", reply_markup=None)
            return
        markup = builder.as_markup()
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data and button.callback_data.startswith("settings:"):
                    callback = SettingsCallback.unpack(button.callback_data)
                    callback.token = token
                    button.callback_data = callback.pack()
        await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        await self._cleanup_service.reset_ttl(
            message.chat.id, message.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
        )

    async def _show_main_menu(self, message: Message, context: SettingsContext) -> None:
        """Show the top-level settings menu."""
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🌐 Languages", callback_data=SettingsCallback(menu=SettingsMenu.LANG).pack()),
            InlineKeyboardButton(text="⚙️ Modules", callback_data=SettingsCallback(menu=SettingsMenu.MODULES).pack()),
        )
        if context.target_chat_id < 0:
            builder.row(
                InlineKeyboardButton(
                    text="🔎 Access & permissions", callback_data=SettingsCallback(menu=SettingsMenu.ACCESS).pack()
                )
            )
        builder.row(
            InlineKeyboardButton(text="❌ Close", callback_data=SettingsCallback(menu=SettingsMenu.CLOSE).pack())
        )
        await self._render(message, "⚙️ <b>Hovorun Settings</b>\n\nSelect a category to configure:", builder, context)

    async def _show_access_menu(self, query: CallbackQuery, context: SettingsContext) -> None:
        """Explain operational access without revealing private ban or entitlement records."""
        message = query.message
        if not isinstance(message, Message):
            return
        status = await self._access_service.features.group_settings_status(context.target_chat_id, query.from_user.id)
        if status is None:
            await query.answer("Settings unavailable. Reopen /settings.", show_alert=True)
            return
        explanations = {
            DecisionReason.ALLOWED: "Group processing is available.",
            DecisionReason.NOT_APPROVED: "Group activation is required. Use /allow_chat.",
            DecisionReason.MEMBER_EVIDENCE_UNKNOWN: "Administrator verification is unavailable or expired.",
            DecisionReason.ADMIN_FULL_BANNED: "Group processing is suspended. Contact a bot administrator privately.",
            DecisionReason.GROUP_BANNED: "Group processing is restricted. Contact a bot administrator privately.",
            DecisionReason.ENTITY_UNAVAILABLE: "This group is unavailable.",
        }
        verification = (
            "Bot permission verification is current."
            if status.bot_verified
            else "Bot permission verification is unavailable or expired. Administrator rights can help verification."
        )
        text = "🔎 <b>Access & permissions</b>\n\n" + explanations.get(
            status.access_reason, "Group access is unavailable."
        )
        text += "\n\n" + verification
        if status.guidance:
            text += "\n\n" + "\n".join(status.guidance)
        text += (
            "\n\nOnly features that require administrator rights need those rights. "
            "Beta activation is explicit in Modules."
        )
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Back", callback_data=SettingsCallback(menu=SettingsMenu.MAIN).pack()))
        await self._render(message, text, builder, context)

    async def _show_lang_menu(self, query: CallbackQuery, context: SettingsContext) -> None:
        """Show language configuration menu."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = context.target_chat_id
        target, ignored = await self._language_service.get_chat_settings(chat_id)

        target_lang = LANGUAGES.get(target)
        target_label = target_lang.display_name if target_lang else target

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=f"🎯 Target: {target_label}",
                callback_data=SettingsCallback(menu=SettingsMenu.LANG_TARGET, page=0).pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=f"🚫 Ignored ({len(ignored)})",
                callback_data=SettingsCallback(menu=SettingsMenu.LANG_IGNORED, page=0).pack(),
            )
        )
        builder.row(InlineKeyboardButton(text="🔙 Back", callback_data=SettingsCallback(menu=SettingsMenu.MAIN).pack()))

        text = "🌐 <b>Language Settings</b>\n\nConfigure translation target and ignored languages:"
        await self._render(msg, text, builder, context)

    async def _show_lang_picker(
        self, message: Message, menu_type: SettingsMenu, page: int, context: SettingsContext
    ) -> None:
        """Show paginated language picker with smart sorting."""
        chat_id = context.target_chat_id
        target, ignored = await self._language_service.get_chat_settings(chat_id)

        # Smart sorting logic
        def get_sort_key(code: str) -> tuple[int, int, str]:
            # 1. Selected status (0 if selected, 1 otherwise)
            is_selected = (menu_type == SettingsMenu.LANG_TARGET and code == target) or (
                menu_type == SettingsMenu.LANG_IGNORED and code in ignored
            )
            selected_rank = 0 if is_selected else 1

            # 2. Popularity rank
            try:
                pop_rank = POPULAR_LANGUAGES.index(code)
            except ValueError:
                pop_rank = 999  # Not in popular list

            # 3. Alphabetical name
            name = LANGUAGES[code].name

            return selected_rank, pop_rank, name

        lang_codes = sorted(LANGUAGES.keys(), key=get_sort_key)

        total_pages = math.ceil(len(lang_codes) / LANGS_PER_PAGE)
        page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0

        start_idx = page * LANGS_PER_PAGE
        page_langs = lang_codes[start_idx : start_idx + LANGS_PER_PAGE]

        builder = InlineKeyboardBuilder()
        for code in page_langs:
            lang = LANGUAGES[code]
            label = lang.display_name
            if menu_type == SettingsMenu.LANG_TARGET:
                is_selected = code == target
                prefix = "✅ " if is_selected else ""
                callback = SettingsCallback(
                    menu=SettingsMenu.LANG_TARGET, action=SettingsAction.SELECT, value=code
                ).pack()
            else:
                is_selected = code in ignored
                prefix = "✅ " if is_selected else "❌ "
                callback = SettingsCallback(
                    menu=SettingsMenu.LANG_IGNORED, action=SettingsAction.TOGGLE, value=code, page=page
                ).pack()

            builder.row(InlineKeyboardButton(text=f"{prefix}{label}", callback_data=callback))

        # Pagination row
        nav_row = []
        if total_pages > 1:
            if page > 0:
                nav_row.append(
                    InlineKeyboardButton(text="⬅️", callback_data=SettingsCallback(menu=menu_type, page=page - 1).pack())
                )
            nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(
                    InlineKeyboardButton(text="➡️", callback_data=SettingsCallback(menu=menu_type, page=page + 1).pack())
                )
            builder.row(*nav_row)

        builder.row(InlineKeyboardButton(text="🔙 Back", callback_data=SettingsCallback(menu=SettingsMenu.LANG).pack()))

        title = (
            "🎯 <b>Select Target Language</b>"
            if menu_type == SettingsMenu.LANG_TARGET
            else "🚫 <b>Toggle Ignored Languages</b>"
        )
        await self._render(message, title, builder, context)

    async def _show_modules_menu(self, query: CallbackQuery, context: SettingsContext) -> None:
        """Show modules (commands) configuration menu with split Video and Post toggles."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = context.target_chat_id
        builder = InlineKeyboardBuilder()

        guidance: set[str] = set()
        for definition in self._access_service.features.registry.all():
            decision = await self._access_service.features.evaluate(
                definition.key, chat_id, query.from_user.id, configure=True
            )
            if decision.guidance:
                guidance.add(decision.guidance)
            if not decision.visible:
                continue
            builder.row(
                InlineKeyboardButton(
                    text=f"{'✅' if decision.selected else '❌'} {definition.display_name}",
                    callback_data=SettingsCallback(
                        menu=SettingsMenu.MODULES, action=SettingsAction.TOGGLE, value=definition.key
                    ).pack(),
                )
            )

        builder.adjust(2)
        builder.row(InlineKeyboardButton(text="🔙 Back", callback_data=SettingsCallback(menu=SettingsMenu.MAIN).pack()))

        text = "⚙️ <b>Modules Settings</b>\n\nToggle Video (🎬) and Image/Text (🖼️) capture per platform:"
        text += "\nEligible beta features start disabled. Select a feature to explicitly enable or disable it."
        if guidance:
            text += "\n\n" + "\n".join(sorted(guidance))
        await self._render(msg, text, builder, context)

    async def handle_callback(self, query: CallbackQuery, callback_data: SettingsCallback) -> None:
        """Route callbacks to appropriate menu handlers."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        if query.from_user.is_bot or query.bot is None:
            await query.answer("Settings unavailable. Reopen /settings.", show_alert=True)
            return
        operation = callback_data.model_copy(update={"token": ""}).pack()
        context = await self._sessions.resolve(
            callback_data.token, operation, query.from_user.id, msg.chat.id, msg.message_id
        )
        if context is None or not await self._authorized(query.from_user.id, context, query.bot):
            await query.answer("Settings unavailable. Reopen /settings.", show_alert=True)
            return
        context = await self._sessions.resolve(
            callback_data.token, operation, query.from_user.id, msg.chat.id, msg.message_id, consume=True
        )
        if context is None:
            await query.answer("Settings unavailable. Reopen /settings.", show_alert=True)
            return
        match callback_data.menu:
            case SettingsMenu.MAIN:
                await self._show_main_menu(msg, context)
            case SettingsMenu.ACCESS:
                await self._show_access_menu(query, context)
            case SettingsMenu.LANG:
                await self._show_lang_menu(query, context)
            case SettingsMenu.MODULES:
                await self._handle_modules_callback(query, callback_data, context)
            case SettingsMenu.LANG_TARGET:
                await self._handle_lang_target_callback(query, callback_data, context)
            case SettingsMenu.LANG_IGNORED:
                await self._handle_lang_ignored_callback(query, callback_data, context)
            case SettingsMenu.CLOSE:
                await self.handle_close(query)

    async def _handle_modules_callback(
        self, query: CallbackQuery, callback_data: SettingsCallback, context: SettingsContext
    ) -> None:
        """Handle module toggle callbacks."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = context.target_chat_id
        if callback_data.action == SettingsAction.TOGGLE and callback_data.value:
            cmd_name = callback_data.value
            decision = await self._access_service.features.evaluate(
                cmd_name, chat_id, query.from_user.id, configure=True
            )
            if not decision.allowed:
                await query.answer(decision.guidance or "Feature unavailable.", show_alert=True)
                return
            definition = self._access_service.features.registry.require(cmd_name)
            if definition.channel == FeatureChannel.BETA and chat_id < 0:
                try:
                    await self._access_service.features.set_group_beta(
                        cmd_name,
                        chat_id,
                        query.from_user.id,
                        enabled=not decision.selected,
                        transport_kind="private" if context.transport_chat_id > 0 else "group",
                    )
                except PermissionError, ValueError:
                    await query.answer("Feature unavailable.", show_alert=True)
                    return
                is_enabled = not decision.selected
            else:
                is_enabled = await self._command_service.toggle(
                    chat_id, cmd_name, actor=ActorContext.telegram(query.from_user.id)
                )
            await query.answer(f"{cmd_name} {'enabled' if is_enabled else 'disabled'}")
        await self._show_modules_menu(query, context)

    async def _handle_lang_target_callback(
        self, query: CallbackQuery, callback_data: SettingsCallback, context: SettingsContext
    ) -> None:
        """Handle target language selection callbacks."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = context.target_chat_id
        if callback_data.action == SettingsAction.SELECT and callback_data.value in LANGUAGES:
            _target, ignored = await self._language_service.get_chat_settings(chat_id)
            await self._language_service.update_settings(
                chat_id, callback_data.value, ignored, actor=ActorContext.telegram(query.from_user.id)
            )
            await query.answer(f"Target set to {callback_data.value}")
            await self._show_lang_menu(query, context)
        else:
            await self._show_lang_picker(msg, SettingsMenu.LANG_TARGET, callback_data.page, context)

    async def _handle_lang_ignored_callback(
        self, query: CallbackQuery, callback_data: SettingsCallback, context: SettingsContext
    ) -> None:
        """Handle ignored language toggle callbacks."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = context.target_chat_id
        if callback_data.action == SettingsAction.TOGGLE and callback_data.value in LANGUAGES:
            target, ignored = await self._language_service.get_chat_settings(chat_id)
            if callback_data.value in ignored:
                ignored.remove(callback_data.value)
            else:
                ignored.append(callback_data.value)

            if "und" not in ignored:
                ignored.append("und")

            await self._language_service.update_settings(
                chat_id, target, ignored, actor=ActorContext.telegram(query.from_user.id)
            )
            await query.answer(f"Toggled {callback_data.value}")
        await self._show_lang_picker(msg, SettingsMenu.LANG_IGNORED, callback_data.page, context)

    async def handle_close(self, query: CallbackQuery) -> None:
        """Handle close button by deleting the window and unregistering from cleanup."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        with contextlib.suppress(Exception):
            await msg.delete()

        await self._cleanup_service.unregister_window(msg.chat.id, msg.message_id)
        await query.answer()

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register settings callbacks."""
        router.callback_query.register(self.handle_callback, SettingsCallback.filter(), flags=flags)
