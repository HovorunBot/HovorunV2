"""Private bot-administrator inspection of bounded audit history."""

import shlex
from datetime import UTC, datetime
from typing import ClassVar

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.operations.audit_inspection import AuditInspectionService, AuditPage
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.access_arguments import AccessArgumentParser

from .base import BaseCommand

AUDIT_HELP = (
    "Use /audit [--type TYPE] [--action ACTION] [--since ISO] [--until ISO] [--limit N] [--before CURSOR] privately."
)
AUDIT_UNAVAILABLE = "Audit inspection unavailable. Use /audit privately."
DEFAULT_BOT_LIMIT = 5
logger = get_logger(__name__)


class AuditCommand(BaseCommand):
    """Show bounded, schema-filtered audit entries without mutating history."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    def __init__(self, administration: AdministrationService, audit: AuditInspectionService) -> None:
        self._administration = administration
        self._audit = audit

    @property
    def name(self) -> str:
        """Stable command identity."""
        return CommandName.AUDIT

    @property
    def trigger(self) -> Command:
        """Private bot-administrator audit entry."""
        return Command(CommandName.AUDIT)

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Query one page for a current private bot administrator."""
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None:
            return
        if message.chat.id != message.from_user.id:
            return
        actor = ActorContext.telegram(message.from_user.id)
        try:
            await self._administration.authorize(actor, message.chat.type)
            args = self._parser().parse_args(shlex.split(message.text or "")[1:])
            page = await self._audit.query(
                actor=actor,
                target_type=args.type,
                action=args.action,
                since=self._timestamp(args.since),
                until=self._timestamp(args.until),
                before=args.before,
                limit=args.limit,
            )
            await message.answer(self._text(page))
            logger.info("Audit page served: actor=%d entries=%d", message.from_user.id, len(page.entries))
        except PermissionError, ValueError:
            logger.warning("Audit command rejected: actor=%d", message.from_user.id)
            await message.answer(AUDIT_UNAVAILABLE)

    def _parser(self) -> AccessArgumentParser:
        parser = AccessArgumentParser(prog="/audit", add_help=False)
        parser.add_argument("--type")
        parser.add_argument("--action")
        parser.add_argument("--since")
        parser.add_argument("--until")
        parser.add_argument("--limit", type=int, default=DEFAULT_BOT_LIMIT)
        parser.add_argument("--before")
        return parser

    def _timestamp(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _text(self, page: AuditPage) -> str:
        lines: list[str] = []
        for entry in page.entries:
            fields = "; ".join(f"{key}={value}" for key, value in entry.fields.items()) or "no fields"
            lines.append(
                f"#{entry.id} {entry.created_at.isoformat()} {entry.target_type}:{entry.target_id} {entry.action}\n"
                f"  actor={entry.actor_origin.value}:{entry.actor_id or '-'} reason={entry.reason or '-'}\n"
                f"  {fields}"
            )
        text = "\n".join(lines) or "No audit entries."
        if page.next_cursor:
            text += f"\nNext: --before {page.next_cursor}"
        return text
