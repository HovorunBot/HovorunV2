"""Private bot-administrator announcement draft composition and previews."""

import html
import shlex
from typing import Any, ClassVar

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.operations.announcements import AnnouncementDraft, AnnouncementService
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.domain.announcement import AnnouncementAudience
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.access_arguments import AccessArgumentParser

from .base import BaseCommand

ANNOUNCE_HELP = (
    "Use /announce draft --category CATEGORY --audience all|users|groups|selected "
    '[--target ID ...] --body "text"\n'
    "Then /announce preview ID, /announce show ID, or /announce edit ID [--category ...] "
    "[--audience ...] [--target ...] [--body ...]."
)
ANNOUNCE_UNAVAILABLE = "Announcement composition unavailable. Use /announce privately."
MIN_ANNOUNCE_TOKENS = 3


class AnnounceCommand(BaseCommand):
    """Compose encrypted drafts and show the exact eligible audience at their revision."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    def __init__(self, administration: AdministrationService, announcements: AnnouncementService) -> None:
        self._administration = administration
        self._announcements = announcements

    @property
    def name(self) -> str:
        """Stable command identity."""
        return CommandName.ANNOUNCE

    @property
    def trigger(self) -> Command:
        """Private bot-administrator announcement entry."""
        return Command(CommandName.ANNOUNCE)

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Dispatch one bounded compose action for a current private bot administrator."""
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None:
            return
        if message.chat.id != message.from_user.id:
            return
        actor = ActorContext.telegram(message.from_user.id)
        try:
            await self._administration.authorize(actor, message.chat.type)
            words = shlex.split(message.text or "")
            if len(words) < MIN_ANNOUNCE_TOKENS:
                await message.answer(ANNOUNCE_HELP)
                return
            await self._dispatch(message, actor, words[1], words[2:])
        except PermissionError, ValueError:
            await message.answer(ANNOUNCE_UNAVAILABLE)

    async def _dispatch(self, message: Message, actor: ActorContext, mode: str, rest: list[str]) -> None:
        if mode == "draft":
            await self._draft(message, actor, rest)
        elif mode == "edit":
            await self._edit(message, actor, rest)
        elif mode == "preview":
            await self._preview(message, rest)
        elif mode == "show":
            await self._show(message, rest)
        else:
            await message.answer(ANNOUNCE_HELP)

    async def _draft(self, message: Message, actor: ActorContext, rest: list[str]) -> None:
        parser = self._composer("/announce draft", body_required=True)
        args = parser.parse_args(rest)
        draft = await self._announcements.create(
            author=actor,
            category=NotificationCategory(args.category),
            audience=AnnouncementAudience(args.audience),
            target_ids=tuple(args.target or ()),
            body=args.body,
        )
        await message.answer(self._draft_text(draft))

    async def _edit(self, message: Message, actor: ActorContext, rest: list[str]) -> None:
        if not rest or not rest[0].isdigit():
            await message.answer(ANNOUNCE_HELP)
            return
        draft, _body = await self._announcements.show(int(rest[0]))
        parser = self._composer("/announce edit", body_required=False)
        args = parser.parse_args(rest[1:])
        updated = await self._announcements.update(
            draft.id,
            author=actor,
            category=NotificationCategory(args.category or draft.category.value),
            audience=AnnouncementAudience(args.audience or draft.audience.value),
            target_ids=tuple(args.target or ()),
            body=args.body,
        )
        await message.answer(self._draft_text(updated))

    async def _preview(self, message: Message, rest: list[str]) -> None:
        if not rest or not rest[0].isdigit():
            await message.answer(ANNOUNCE_HELP)
            return
        preview = await self._announcements.preview(int(rest[0]))
        await message.answer(
            f"Revision {preview.revision} preview: {preview.users} user(s), "
            f"{preview.groups} group(s), {preview.total} total."
        )

    async def _show(self, message: Message, rest: list[str]) -> None:
        if not rest or not rest[0].isdigit():
            await message.answer(ANNOUNCE_HELP)
            return
        draft, body = await self._announcements.show(int(rest[0]))
        await message.answer(html.escape(body))
        await message.answer(self._draft_text(draft))

    def _composer(self, prog: str, *, body_required: bool) -> AccessArgumentParser:
        parser = AccessArgumentParser(prog=prog, add_help=False)
        parser.add_argument("--category", choices=[c.value for c in NotificationCategory])
        parser.add_argument("--audience", choices=[a.value for a in AnnouncementAudience])
        parser.add_argument("--target", action="append", type=int)
        parser.add_argument("--body", required=body_required)
        return parser

    def _draft_text(self, draft: AnnouncementDraft) -> str:
        return (
            f"Draft #{draft.id} revision {draft.revision} · {draft.category.value} · "
            f"{draft.audience.value} · preview {draft.preview_count} recipient(s)."
        )
