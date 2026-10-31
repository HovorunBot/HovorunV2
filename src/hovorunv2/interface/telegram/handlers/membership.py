"""Policy-relevant membership events, scoped to current active identity generations."""

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, ChatMemberUpdated

from hovorunv2.application.identity.identity import GroupIdentityStore
from hovorunv2.application.identity.membership import BotCapabilityEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.interface.telegram.membership import MEMBERSHIP_FRESHNESS, membership_status


class MembershipEventHandler:
    """Track administrators and physical bot presence without storing ordinary member lists."""

    def __init__(self, identities: GroupIdentityStore, memberships: MembershipStore) -> None:
        """Receive mandatory generation-aware identity and evidence boundaries."""
        self._identities = identities
        self._memberships = memberships

    def _evidence(self, event: ChatMemberUpdated) -> MembershipEvidence:
        return MembershipEvidence(
            group_telegram_id=event.chat.id,
            user_telegram_id=event.new_chat_member.user.id,
            status=membership_status(event.new_chat_member),
            verified_at=event.date,
            expires_at=event.date + MEMBERSHIP_FRESHNESS,
        )

    def _bot_evidence(self, event: ChatMemberUpdated) -> BotCapabilityEvidence:
        member = event.new_chat_member
        evidence = self._evidence(event)
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
        if evidence.status == MembershipStatus.UNKNOWN:
            return BotCapabilityEvidence(evidence, None, None, None, None, None)
        return BotCapabilityEvidence(
            evidence,
            can_delete_messages=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_chat=False,
        )

    async def handle_member(self, event: ChatMemberUpdated) -> None:
        """Apply evidence only for human administrators or already recorded relevant identities."""
        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP} or event.new_chat_member.user.is_bot:
            return
        group = await self._identities.ingest_group(event.chat.id, event.chat.title, event.chat.type)
        if group is not None:
            await self._memberships.save_member(
                group.id, self._evidence(event), ActorContext.telegram(event.from_user.id)
            )

    async def handle_bot(self, event: ChatMemberUpdated, bot: Bot) -> None:
        """Record actual bot presence separately; unknown checks never mean removal."""
        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP} or event.new_chat_member.user.id != bot.id:
            return
        group = await self._identities.ingest_group(event.chat.id, event.chat.title, event.chat.type)
        if group is not None:
            await self._memberships.save_bot(
                group.id, self._bot_evidence(event), ActorContext.telegram(event.from_user.id)
            )

    def register(self, router: Router) -> None:
        """Subscribe explicitly to human member changes; BotJoinHandler owns my_chat_member dispatch."""
        router.chat_member.register(self.handle_member)
