"""Unified settings command with hierarchical paginated menus."""

import contextlib
import math
from typing import Any, Final

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.data.constants import TOGGLEABLE_COMMANDS, CommandName
from hovorunv2.application.data.languages import LANGUAGES, POPULAR_LANGUAGES
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import SettingsAction, SettingsCallback, SettingsMenu

from .base import BaseCommand

logger = get_logger(__name__)

CONFIG_WINDOW_TTL: Final = 30
LANGS_PER_PAGE: Final = 10


class SettingsCommand(BaseCommand):
    """Unified settings command replacing legacy configuration commands."""

    def __init__(
        self,
        language_service: LanguageService,
        command_service: CommandService,
        access_service: AccessService,
        cleanup_service: CleanupService,
    ) -> None:
        """Initialize command."""
        self._language_service = language_service
        self._command_service = command_service
        self._access_service = access_service
        self._cleanup_service = cleanup_service

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.SETTINGS

    @property
    def policy(self) -> CommandPolicy:
        """Admins only, whitelisted chats only."""
        return CommandPolicy(
            requires_admin=False,
            requires_group_admin=True,
            requires_whitelist=True,
            is_toggleable=False,
        )

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /settings."""
        return bool(message.text and message.text.strip().startswith("/settings"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ARG002
        """Handle /settings command."""
        await self._show_main_menu(message, bot)

    async def _show_main_menu(self, message: Message | CallbackQuery, bot: Bot | None) -> None:
        """Show the top-level settings menu."""
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🌐 Languages", callback_data=SettingsCallback(menu=SettingsMenu.LANG).pack()),
            InlineKeyboardButton(text="⚙️ Modules", callback_data=SettingsCallback(menu=SettingsMenu.MODULES).pack()),
        )
        builder.row(InlineKeyboardButton(text="❌ Close", callback_data="config_close"))

        text = "⚙️ <b>Hovorun Settings</b>\n\nSelect a category to configure:"

        if isinstance(message, Message):
            msg = await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
            if bot:
                await self._cleanup_service.replace_singleton_window(
                    bot, msg.chat.id, msg.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
                )
        else:
            msg = message.message
            if not msg or not isinstance(msg, Message):
                return
            await msg.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
            await self._cleanup_service.reset_ttl(
                msg.chat.id, msg.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
            )

    async def _show_lang_menu(self, query: CallbackQuery) -> None:
        """Show language configuration menu."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = msg.chat.id
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
        await msg.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await self._cleanup_service.reset_ttl(
            chat_id, msg.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
        )

    async def _show_lang_picker(self, message: Message, menu_type: SettingsMenu, page: int) -> None:
        """Show paginated language picker with smart sorting."""
        chat_id = message.chat.id
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
        await message.edit_text(title, parse_mode="HTML", reply_markup=builder.as_markup())
        await self._cleanup_service.reset_ttl(
            chat_id, message.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
        )

    async def _show_modules_menu(self, query: CallbackQuery) -> None:
        """Show modules (commands) configuration menu."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = msg.chat.id
        builder = InlineKeyboardBuilder()

        for cmd_name in TOGGLEABLE_COMMANDS:
            is_enabled = await self._command_service.is_command_allowed(chat_id, cmd_name)
            prefix = "✅ " if is_enabled else "❌ "
            builder.row(
                InlineKeyboardButton(
                    text=f"{prefix}{cmd_name.capitalize()}",
                    callback_data=SettingsCallback(
                        menu=SettingsMenu.MODULES, action=SettingsAction.TOGGLE, value=cmd_name
                    ).pack(),
                )
            )

        builder.row(InlineKeyboardButton(text="🔙 Back", callback_data=SettingsCallback(menu=SettingsMenu.MAIN).pack()))

        text = "⚙️ <b>Modules Settings</b>\n\nEnable or disable specific bot features:"
        await msg.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await self._cleanup_service.reset_ttl(
            chat_id, msg.message_id, ttl=CONFIG_WINDOW_TTL, cleanup_type=CommandName.SETTINGS
        )

    async def handle_callback(self, query: CallbackQuery, callback_data: SettingsCallback) -> None:
        """Route callbacks to appropriate menu handlers."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        menu = callback_data.menu

        if menu == SettingsMenu.MAIN:
            await self._show_main_menu(query, None)
        elif menu == SettingsMenu.LANG:
            await self._show_lang_menu(query)
        elif menu == SettingsMenu.MODULES:
            await self._handle_modules_callback(query, callback_data)
        elif menu == SettingsMenu.LANG_TARGET:
            await self._handle_lang_target_callback(query, callback_data)
        elif menu == SettingsMenu.LANG_IGNORED:
            await self._handle_lang_ignored_callback(query, callback_data)

    async def _handle_modules_callback(self, query: CallbackQuery, callback_data: SettingsCallback) -> None:
        """Handle module toggle callbacks."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = msg.chat.id
        if callback_data.action == SettingsAction.TOGGLE and callback_data.value:
            cmd_name = callback_data.value
            is_enabled = await self._command_service.is_command_allowed(chat_id, cmd_name)
            if is_enabled:
                await self._command_service.disable_command(chat_id, cmd_name)
            else:
                await self._command_service.enable_command(chat_id, cmd_name)
            await query.answer(f"{cmd_name} {'enabled' if not is_enabled else 'disabled'}")
        await self._show_modules_menu(query)

    async def _handle_lang_target_callback(self, query: CallbackQuery, callback_data: SettingsCallback) -> None:
        """Handle target language selection callbacks."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = msg.chat.id
        if callback_data.action == SettingsAction.SELECT and callback_data.value:
            _target, ignored = await self._language_service.get_chat_settings(chat_id)
            await self._language_service.update_settings(chat_id, callback_data.value, ignored)
            await query.answer(f"Target set to {callback_data.value}")
            await self._show_lang_menu(query)
        else:
            await self._show_lang_picker(msg, SettingsMenu.LANG_TARGET, callback_data.page)

    async def _handle_lang_ignored_callback(self, query: CallbackQuery, callback_data: SettingsCallback) -> None:
        """Handle ignored language toggle callbacks."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        chat_id = msg.chat.id
        if callback_data.action == SettingsAction.TOGGLE and callback_data.value:
            target, ignored = await self._language_service.get_chat_settings(chat_id)
            if callback_data.value in ignored:
                ignored.remove(callback_data.value)
            else:
                ignored.append(callback_data.value)

            if "und" not in ignored:
                ignored.append("und")

            await self._language_service.update_settings(chat_id, target, ignored)
            await query.answer(f"Toggled {callback_data.value}")
        await self._show_lang_picker(msg, SettingsMenu.LANG_IGNORED, callback_data.page)

    async def handle_close(self, query: CallbackQuery) -> None:
        """Handle close button by deleting the window and unregistering from cleanup."""
        msg = query.message
        if not msg or not isinstance(msg, Message):
            return

        with contextlib.suppress(Exception):
            await msg.delete()

        await self._cleanup_service.unregister_window(msg.chat.id, msg.message_id)
        await query.answer()

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:  # noqa: ARG002
        """Register settings callbacks."""
        router.callback_query.register(self.handle_callback, SettingsCallback.filter())
        router.callback_query.register(self.handle_close, lambda c: c.data == "config_close")
