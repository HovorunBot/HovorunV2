"""Telegram membership adapter with explicit API uncertainty and separate rights."""

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberRestricted,
    ChatMemberUnion,
)

from hovorunv2.application.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)

MEMBERSHIP_FRESHNESS = timedelta(minutes=5)
MEMBERSHIP_REQUEST_TIMEOUT = 10


def membership_status(member: ChatMemberUnion) -> MembershipStatus:
    """Translate Telegram variants, counting creators as administrators."""
    if isinstance(member, (ChatMemberOwner, ChatMemberAdministrator)):
        return MembershipStatus.ADMIN
    if isinstance(member, ChatMemberMember):
        return MembershipStatus.MEMBER
    if isinstance(member, ChatMemberRestricted):
        return MembershipStatus.MEMBER if member.is_member else MembershipStatus.LEFT
    if isinstance(member, (ChatMemberLeft, ChatMemberBanned)):
        return MembershipStatus.LEFT
    return MembershipStatus.UNKNOWN


class TelegramMembershipAdapter:
    """Query selected identities only; failed Telegram calls never prove absence."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    def _evidence(
        self, group_telegram_id: int, user_telegram_id: int, status: MembershipStatus, verified_at: datetime
    ) -> MembershipEvidence:
        return MembershipEvidence(
            group_telegram_id,
            user_telegram_id,
            status,
            verified_at,
            verified_at + MEMBERSHIP_FRESHNESS,
        )

    async def verify_member(self, group_telegram_id: int, user_telegram_id: int) -> MembershipEvidence:
        """Return API-observed membership; network, permission and API errors stay UNKNOWN."""
        now = datetime.now(UTC)
        try:
            member = await self._bot.get_chat_member(
                group_telegram_id, user_telegram_id, request_timeout=MEMBERSHIP_REQUEST_TIMEOUT
            )
        except TelegramAPIError:
            status = MembershipStatus.UNKNOWN
        else:
            status = membership_status(member) if member.user.id == user_telegram_id else MembershipStatus.UNKNOWN
        return self._evidence(group_telegram_id, user_telegram_id, status, now)

    async def list_administrators(self, group_telegram_id: int) -> AdministratorEvidence:
        """Fetch numeric human administrator evidence without storing public/private profile fields."""
        now = datetime.now(UTC)
        try:
            administrators = await self._bot.get_chat_administrators(
                group_telegram_id, request_timeout=MEMBERSHIP_REQUEST_TIMEOUT
            )
        except TelegramAPIError:
            return AdministratorEvidence(
                group_telegram_id, known=False, members=(), verified_at=now, expires_at=now + MEMBERSHIP_FRESHNESS
            )
        members = tuple(
            self._evidence(group_telegram_id, member.user.id, membership_status(member), now)
            for member in administrators
            if not member.user.is_bot and membership_status(member) == MembershipStatus.ADMIN
        )
        return AdministratorEvidence(
            group_telegram_id, known=True, members=members, verified_at=now, expires_at=now + MEMBERSHIP_FRESHNESS
        )

    async def verify_bot(self, group_telegram_id: int) -> BotCapabilityEvidence:
        """Check rights individually; ordinary presence never implies administrator capabilities."""
        now = datetime.now(UTC)
        try:
            member = await self._bot.get_chat_member(
                group_telegram_id, self._bot.id, request_timeout=MEMBERSHIP_REQUEST_TIMEOUT
            )
        except TelegramAPIError:
            member = None
        status = membership_status(member) if member is not None else MembershipStatus.UNKNOWN
        if member is not None and member.user.id != self._bot.id:
            status = MembershipStatus.UNKNOWN
        evidence = self._evidence(group_telegram_id, self._bot.id, status, now)
        if status == MembershipStatus.UNKNOWN:
            return BotCapabilityEvidence(evidence, None, None, None, None, None)
        if isinstance(member, ChatMemberOwner):
            return BotCapabilityEvidence(
                evidence,
                can_delete_messages=True,
                can_restrict_members=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_manage_chat=True,
            )
        if isinstance(member, ChatMemberAdministrator):
            return BotCapabilityEvidence(
                evidence,
                member.can_delete_messages,
                member.can_restrict_members,
                member.can_invite_users,
                member.can_pin_messages,
                member.can_manage_chat,
            )
        return BotCapabilityEvidence(
            evidence,
            can_delete_messages=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_chat=False,
        )
