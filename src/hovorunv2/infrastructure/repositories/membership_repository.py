"""Session-scoped evidence queries and mutations; retain only policy-relevant members."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.selectable import Exists

from hovorunv2.application.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.group_grant import GroupSponsorDB
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


def evidence_time(value: datetime) -> datetime:
    """Normalize timestamps read from SQLite, whose datetime storage omits timezone metadata."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_evidence_time(verified_at: datetime, expires_at: datetime) -> None:
    """Reject ambiguous or inverted evidence before durable state changes."""
    if verified_at.tzinfo is None or expires_at.tzinfo is None or expires_at < verified_at:
        msg = "Evidence requires aware timestamps and expiry at or after verification"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MembershipMutation:
    """Explicit nonprivate audit changes for one atomic update."""

    changed: bool
    changes: dict[str, object] = field(default_factory=dict)


class SQLAlchemyMembershipRepository:
    """Bind evidence to current numeric identity and generation without an ordinary member directory."""

    def __init__(self, session: AsyncSession) -> None:
        """Use caller-owned transaction."""
        self._session = session

    def _sponsor_exists(self, group_id: int | InstrumentedAttribute[int]) -> Exists:
        return exists(
            select(GroupSponsorDB.id)
            .where(
                GroupSponsorDB.group_id == group_id,
                GroupSponsorDB.user_id == UserDB.id,
                GroupSponsorDB.deleted_at.is_(None),
                GroupSponsorDB.revoked_at.is_(None),
            )
            .correlate(UserDB, ChatDB)
        )

    async def due_groups(self, now: datetime, limit: int) -> list[ChatDB]:
        """Bound all work in SQL; unknown evidence retries on its expiry."""
        if limit < 1:
            return []
        relevant_expired = exists(
            select(UserDB.id)
            .outerjoin(
                MemberEvidenceDB,
                (MemberEvidenceDB.user_id == UserDB.id)
                & (MemberEvidenceDB.group_id == ChatDB.id)
                & MemberEvidenceDB.deleted_at.is_(None),
            )
            .where(
                UserDB.deleted_at.is_(None),
                or_(
                    self._sponsor_exists(ChatDB.id),
                    MemberEvidenceDB.admin_candidate.is_(True),
                    MemberEvidenceDB.needs_verification.is_(True),
                ),
                or_(MemberEvidenceDB.id.is_(None), MemberEvidenceDB.expires_at <= now),
            )
            .correlate(ChatDB)
        )
        query = (
            select(ChatDB)
            .outerjoin(GroupMembershipStateDB, GroupMembershipStateDB.group_id == ChatDB.id)
            .where(
                ChatDB.deleted_at.is_(None),
                ChatDB.chat_type.in_(("group", "supergroup")),
                or_(
                    GroupMembershipStateDB.group_id.is_(None),
                    GroupMembershipStateDB.deleted_at.is_not(None),
                    GroupMembershipStateDB.admins_expires_at.is_(None),
                    GroupMembershipStateDB.admins_expires_at <= now,
                    GroupMembershipStateDB.bot_expires_at.is_(None),
                    GroupMembershipStateDB.bot_expires_at <= now,
                    relevant_expired,
                ),
            )
            .order_by(GroupMembershipStateDB.admins_expires_at.asc().nulls_first(), ChatDB.id)
            .limit(limit)
        )
        return list((await self._session.scalars(query)).all())

    async def relevant_users(self, group_id: int, limit: int) -> list[UserDB]:
        """Bound sponsors/admin candidates and prioritize missing/expired records."""
        if limit < 1:
            return []
        query = (
            select(UserDB)
            .outerjoin(
                MemberEvidenceDB,
                (MemberEvidenceDB.user_id == UserDB.id)
                & (MemberEvidenceDB.group_id == group_id)
                & MemberEvidenceDB.deleted_at.is_(None),
            )
            .where(
                UserDB.deleted_at.is_(None),
                exists(select(ChatDB.id).where(ChatDB.id == group_id, ChatDB.deleted_at.is_(None))),
                or_(
                    self._sponsor_exists(group_id),
                    MemberEvidenceDB.admin_candidate.is_(True),
                    MemberEvidenceDB.needs_verification.is_(True),
                ),
            )
            .order_by(MemberEvidenceDB.expires_at.asc().nulls_first(), UserDB.id)
            .limit(limit)
        )
        return list((await self._session.scalars(query)).all())

    async def _group(self, group_id: int, telegram_id: int) -> ChatDB | None:
        group = await self._session.get(ChatDB, group_id)
        if group is None or group.deleted_at is not None or group.chat_type not in {"group", "supergroup"}:
            return None
        if group.chat_id != telegram_id:
            msg = "Membership evidence does not match the group identity generation"
            raise ValueError(msg)
        return group

    async def _state(self, group_id: int) -> GroupMembershipStateDB:
        state = await self._session.get(GroupMembershipStateDB, group_id)
        if state is None:
            state = GroupMembershipStateDB(group_id=group_id, policy_revision=0)
            self._session.add(state)
        return state

    async def get_state(self, group_id: int) -> GroupMembershipStateDB | None:
        """Read active-generation freshness and revision state."""
        return await self._session.scalar(
            select(GroupMembershipStateDB)
            .join(
                ChatDB,
                ChatDB.id == GroupMembershipStateDB.group_id,
            )
            .where(
                GroupMembershipStateDB.group_id == group_id,
                GroupMembershipStateDB.deleted_at.is_(None),
                ChatDB.deleted_at.is_(None),
            )
        )

    async def _record(self, group_id: int, user_id: int) -> MemberEvidenceDB | None:
        return await self._session.scalar(
            select(MemberEvidenceDB).where(
                MemberEvidenceDB.group_id == group_id,
                MemberEvidenceDB.user_id == user_id,
                MemberEvidenceDB.deleted_at.is_(None),
            )
        )

    async def get_member(self, group_id: int, user_id: int) -> MembershipEvidence | None:
        """Read evidence only when both identity generations remain active."""
        row = await self._record(group_id, user_id)
        group = await self._session.get(ChatDB, group_id)
        user = await self._session.get(UserDB, user_id)
        if row is None or group is None or group.deleted_at is not None or user is None or user.deleted_at is not None:
            return None
        return MembershipEvidence(
            group.chat_id,
            user.telegram_id,
            MembershipStatus(row.status),
            evidence_time(row.verified_at),
            evidence_time(row.expires_at),
        )

    async def _is_sponsor(self, group_id: int, user_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists(
                        select(GroupSponsorDB.id).where(
                            GroupSponsorDB.group_id == group_id,
                            GroupSponsorDB.user_id == user_id,
                            GroupSponsorDB.deleted_at.is_(None),
                            GroupSponsorDB.revoked_at.is_(None),
                        )
                    )
                )
            )
        )

    async def _save_member(
        self,
        group: ChatDB,
        evidence: MembershipEvidence,
        expected_user_id: int | None = None,
    ) -> bool:
        user_repo = SQLAlchemyUserRepository(self._session)
        user = await user_repo.get_by_telegram_id(evidence.user_telegram_id, str(group.platform))
        if user is None and evidence.status == MembershipStatus.ADMIN:
            user, _ = await user_repo.ingest(evidence.user_telegram_id, str(group.platform))
        if user is None or (expected_user_id is not None and user.id != expected_user_id):
            return False
        latest = await self._session.scalar(
            select(MemberEvidenceDB)
            .where(
                MemberEvidenceDB.group_id == group.id,
                MemberEvidenceDB.user_id == user.id,
            )
            .order_by(MemberEvidenceDB.verified_at.desc(), MemberEvidenceDB.id.desc())
            .limit(1)
        )
        if latest is not None and evidence.verified_at <= evidence_time(latest.verified_at):
            return False
        if user.generation > 1 and evidence.verified_at < evidence_time(user.created_at):
            return False
        row = await self._record(group.id, user.id)
        sponsor = await self._is_sponsor(group.id, user.id)
        if row is None and not sponsor and evidence.status != MembershipStatus.ADMIN:
            return False
        if row is not None and evidence.verified_at <= evidence_time(row.verified_at):
            return False
        if row is None:
            row = MemberEvidenceDB(
                group_id=group.id,
                user_id=user.id,
                status=evidence.status.value,
                verified_at=evidence.verified_at,
                expires_at=evidence.expires_at,
            )
            self._session.add(row)
        row.status = evidence.status.value
        row.verified_at = evidence.verified_at
        row.expires_at = evidence.expires_at
        if evidence.status != MembershipStatus.UNKNOWN:
            row.admin_candidate = evidence.status == MembershipStatus.ADMIN
            row.needs_verification = False
            if not row.admin_candidate and not sponsor:
                row.deleted_at = evidence.verified_at
        return True

    async def save_member(
        self,
        group_id: int,
        evidence: MembershipEvidence,
        expected_user_id: int | None = None,
    ) -> MembershipMutation:
        """Persist a relevant event/API check only if newer than the current evidence."""
        validate_evidence_time(evidence.verified_at, evidence.expires_at)
        group = await self._group(group_id, evidence.group_telegram_id)
        if group is None or not await self._save_member(group, evidence, expected_user_id):
            return MembershipMutation(changed=False)
        state = await self._state(group_id)
        state.policy_revision += 1
        return MembershipMutation(
            changed=True,
            changes={
                "group_id": group_id,
                "user_telegram_id": evidence.user_telegram_id,
                "status": evidence.status.value,
                "policy_revision": state.policy_revision,
            },
        )

    async def save_bot(self, group_id: int, evidence: BotCapabilityEvidence) -> MembershipMutation:
        """Presence and per-capability evidence never overwrite ChatStatus or access bases."""
        member = evidence.membership
        validate_evidence_time(member.verified_at, member.expires_at)
        group = await self._group(group_id, member.group_telegram_id)
        if group is None:
            return MembershipMutation(changed=False)
        state = await self._state(group_id)
        if state.bot_verified_at is not None and member.verified_at <= evidence_time(state.bot_verified_at):
            return MembershipMutation(changed=False)
        state.bot_status = member.status.value
        state.bot_telegram_id = member.user_telegram_id
        state.bot_verified_at, state.bot_expires_at = member.verified_at, member.expires_at
        state.can_delete_messages = evidence.can_delete_messages
        state.can_restrict_members = evidence.can_restrict_members
        state.can_invite_users = evidence.can_invite_users
        state.can_pin_messages = evidence.can_pin_messages
        state.can_manage_chat = evidence.can_manage_chat
        if member.status != MembershipStatus.UNKNOWN:
            group.is_bot_present = member.status != MembershipStatus.LEFT
            group.bot_capabilities_verified_at = member.verified_at
        state.policy_revision += 1
        return MembershipMutation(
            changed=True,
            changes={"group_id": group_id, "bot_status": member.status.value, "policy_revision": state.policy_revision},
        )

    async def save_admins(self, group_id: int, evidence: AdministratorEvidence) -> MembershipMutation:
        """Reconcile only admins; failed listings mark retained candidates UNKNOWN, never LEFT."""
        validate_evidence_time(evidence.verified_at, evidence.expires_at)
        group = await self._group(group_id, evidence.group_telegram_id)
        if group is None:
            return MembershipMutation(changed=False)
        for member in evidence.members:
            validate_evidence_time(member.verified_at, member.expires_at)
            if member.group_telegram_id != group.chat_id or member.status != MembershipStatus.ADMIN:
                msg = "Administrator snapshot contains mismatched or nonadministrator evidence"
                raise ValueError(msg)
        state = await self._state(group_id)
        if state.admins_verified_at is not None and evidence.verified_at <= evidence_time(state.admins_verified_at):
            return MembershipMutation(changed=False)
        prior = list(
            (
                await self._session.scalars(
                    select(MemberEvidenceDB).where(
                        MemberEvidenceDB.group_id == group_id,
                        MemberEvidenceDB.deleted_at.is_(None),
                        or_(MemberEvidenceDB.admin_candidate.is_(True), MemberEvidenceDB.needs_verification.is_(True)),
                    )
                )
            ).all()
        )
        current_ids = {member.user_telegram_id for member in evidence.members} if evidence.known else set()
        for row in prior:
            user = await self._session.get(UserDB, row.user_id)
            if user is None or user.deleted_at is not None or evidence.verified_at <= evidence_time(row.verified_at):
                continue
            if not evidence.known or user.telegram_id not in current_ids:
                row.status = MembershipStatus.UNKNOWN.value
                row.verified_at = evidence.verified_at
                row.expires_at = evidence.expires_at if not evidence.known else evidence.verified_at
                row.needs_verification = True
                if evidence.known:
                    row.admin_candidate = False
        if evidence.known:
            for member in evidence.members:
                await self._save_member(group, member)
        state.admins_known = evidence.known
        state.admins_verified_at, state.admins_expires_at = evidence.verified_at, evidence.expires_at
        state.policy_revision += 1
        return MembershipMutation(
            changed=True,
            changes={
                "group_id": group_id,
                "admins_known": evidence.known,
                "admin_count": len(current_ids),
                "policy_revision": state.policy_revision,
            },
        )
