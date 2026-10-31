"""Small native PostgreSQL adapters for worker policy reads and durable accounting."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from hovorunv2.application.access.permissions import AccessDecision, PermissionRequest, PermissionTarget
from hovorunv2.application.features.feature_policy import FeatureDecision, evaluate_feature
from hovorunv2.application.features.registry import FeatureRegistry, TriggerKind
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.ports import AuthorizationPolicy
from hovorunv2.application.usage.policies import ResolvedUsage, UsageSafetyCaps, UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import (
    AccountingSubject,
    AppliedUsage,
    UsageEvent,
    usage_audit_changes,
    usage_batch_id,
    validate_usage_batch,
)
from hovorunv2.application.usage.usage_resolution import resolve_usage
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.audit_schema import validate_audit_changes, validate_audit_reason
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.system import SystemStateDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.audited_transaction import (
    MAX_TRANSACTION_ATTEMPTS,
    RETRYABLE_TRANSACTION_STATES,
)
from hovorunv2.infrastructure.persistence.repositories.membership_repository import (
    evidence_time,
    validate_evidence_time,
)
from hovorunv2.infrastructure.persistence.repositories.policy_fact_reader import PolicyFactReader
from hovorunv2.infrastructure.persistence.repositories.policy_queries import exact_target
from hovorunv2.infrastructure.persistence.repositories.usage_ledger import UsageLedgerRepository


class WorkerPolicyStore:
    """Read fresh facts in short sessions; policy snapshots never authorize later execution."""

    def __init__(
        self,
        sessions: sessionmaker,
        policy: AuthorizationPolicy,
        registry: FeatureRegistry,
        safety: UsageSafetyCaps,
        require_owner: Callable[[], None],
    ) -> None:
        self._sessions = sessions
        self._policy = policy
        self._registry = registry
        self._safety = safety
        self._require_owner = require_owner

    def evaluate(
        self,
        feature: str,
        chat_id: int,
        actor_id: int | None,
        *,
        trigger: TriggerKind = TriggerKind.DIRECT,
        configure: bool = False,
    ) -> FeatureDecision:
        """Apply the bot's exact evaluator and database facts without an event loop."""
        self._require_owner()
        definition = self._registry.get(feature)
        if definition is None:
            return FeatureDecision()
        with self._sessions() as session:
            facts = PolicyFactReader(session, self._policy).feature_facts(
                definition,
                chat_id,
                actor_id,
                trigger,
                configure=configure,
                now=datetime.now(UTC),
            )
            return evaluate_feature(definition, facts, trigger=trigger, configure=configure)

    def active_target(self, target: PermissionTarget) -> bool:
        """Validate a queued reference without converting a prior decision into an access grant."""
        self._require_owner()
        statement = exact_target(target)
        if statement is None:
            return False
        with self._sessions() as session:
            return session.scalar(statement) is not None

    def permission(self, request: PermissionRequest) -> AccessDecision:
        """Check current active authority and exact target generation."""
        self._require_owner()
        with self._sessions() as session:
            return PolicyFactReader(session, self._policy).permission(request, datetime.now(UTC))

    def chat_translation_settings(self, chat_id: int) -> tuple[str | None, tuple[str, ...] | None] | None:
        """Load fresh translation settings through the same child-owned session boundary as policy reads."""
        self._require_owner()
        with self._sessions() as session:
            return PolicyFactReader(session, self._policy).chat_translation_settings(chat_id)

    def resolve(
        self,
        feature: str,
        trigger: TriggerKind,
        actor_id: int | None = None,
        *,
        group_id: int | None = None,
        unit: UsageUnit = UsageUnit.REQUESTS,
    ) -> ResolvedUsage:
        """Resolve charged subject and intersect restrictions using the shared pure rule."""
        self._require_owner()
        definition = self._registry.require(feature)
        if trigger not in definition.triggers or unit not in definition.units:
            msg = "Feature does not support this usage trigger or unit."
            raise ValueError(msg)
        with self._sessions() as session:
            reader = PolicyFactReader(session, self._policy)
            subject, trusted, bypass = reader.usage_subject(actor_id, group_id, trigger)
            return resolve_usage(
                definition,
                subject,
                reader.usage_overrides(subject),
                self._safety,
                trusted=trusted,
                bypass=bypass,
                unit=unit,
            )


class WorkerUsageLedgerStore:
    """Native transaction ownership; the shared repository owns all receipt and counter semantics."""

    def __init__(
        self,
        sessions: sessionmaker,
        registry: FeatureRegistry,
        require_owner: Callable[[], None],
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._require_owner = require_owner

    def apply_batch(self, events: tuple[UsageEvent, ...], *, actor: ActorContext) -> AppliedUsage:
        """Commit receipts, guarded upserts and payload-free audit together, with bounded DB retries."""
        self._require_owner()
        validate_usage_batch(self._registry, events, actor)
        validate_audit_reason(actor.reason)
        for attempt in range(MAX_TRANSACTION_ATTEMPTS):
            try:
                return self._apply_usage_attempt(events, actor)
            except DBAPIError as error:
                if (
                    getattr(error.orig, "sqlstate", None) not in RETRYABLE_TRANSACTION_STATES
                    or attempt == MAX_TRANSACTION_ATTEMPTS - 1
                ):
                    raise
        msg = "Accounting transaction exhausted its attempts."
        raise ValueError(msg)

    def _apply_usage_attempt(self, events: tuple[UsageEvent, ...], actor: ActorContext) -> AppliedUsage:
        with self._sessions.begin() as session:
            result = UsageLedgerRepository(session).apply(events)
            if result.events_applied:
                self._append_audit_event(session, events, actor, result)
            return result

    def _append_audit_event(
        self, session: Session, events: tuple[UsageEvent, ...], actor: ActorContext, result: AppliedUsage
    ) -> None:
        changes = usage_audit_changes(result)
        validate_audit_changes("usage_batch", "apply", changes)
        session.add(
            AuditEventDB(
                actor_origin=actor.origin,
                actor_id=actor.actor_id,
                target_type="usage_batch",
                target_id=usage_batch_id(events),
                action="apply",
                changed_fields=changes,
                reason=actor.reason,
                correlation_id=actor.correlation_id,
            )
        )

    def total(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> int:
        """Account retained historical subjects without granting execution rights."""
        self._require_owner()
        with self._sessions() as session:
            repository = UsageLedgerRepository(session)
            repository.require_known_subject(subject)
            row = repository.aggregate(subject, bucket, unit, window, timestamp)
            return row.amount if row is not None else 0


class WorkerOperationsStore:
    """Bounded synchronous operations persistence for native maintenance tasks."""

    def __init__(self, sessions: sessionmaker, require_owner: Callable[[], None]) -> None:
        self._sessions = sessions
        self._require_owner = require_owner

    def due_groups(self, now: datetime, limit: int = 10) -> list[ChatDB]:
        """Bounded active groups with missing/expired bot, admin or relevant user evidence."""
        self._require_owner()
        if limit < 1:
            return []
        relevant_expired = exists(
            select(UserDB.id)
            .join(
                MemberEvidenceDB,
                (MemberEvidenceDB.user_id == UserDB.id)
                & (MemberEvidenceDB.group_id == ChatDB.id)
                & MemberEvidenceDB.deleted_at.is_(None),
            )
            .where(
                UserDB.deleted_at.is_(None),
                or_(
                    MemberEvidenceDB.admin_candidate.is_(True),
                    MemberEvidenceDB.needs_verification.is_(True),
                ),
                MemberEvidenceDB.expires_at <= now,
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
        with self._sessions() as session:
            return list(session.scalars(query).all())

    def relevant_users(self, group_id: int, limit: int = 50) -> list[UserDB]:
        """Return current and unresolved former administrators, missing/stale evidence first."""
        self._require_owner()
        if limit < 1:
            return []
        query = (
            select(UserDB)
            .join(
                MemberEvidenceDB,
                (MemberEvidenceDB.user_id == UserDB.id)
                & (MemberEvidenceDB.group_id == group_id)
                & MemberEvidenceDB.deleted_at.is_(None),
            )
            .where(
                UserDB.deleted_at.is_(None),
                exists(select(ChatDB.id).where(ChatDB.id == group_id, ChatDB.deleted_at.is_(None))),
                or_(
                    MemberEvidenceDB.admin_candidate.is_(True),
                    MemberEvidenceDB.needs_verification.is_(True),
                ),
            )
            .order_by(MemberEvidenceDB.expires_at.asc().nulls_first(), UserDB.id)
            .limit(limit)
        )
        with self._sessions() as session:
            return list(session.scalars(query).all())

    def save_bot(
        self,
        group_id: int,
        evidence: BotCapabilityEvidence,
        actor: ActorContext | None = None,
    ) -> bool:
        """Persist bot presence and capabilities with audited state mutation."""
        self._require_owner()
        member = evidence.membership
        validate_evidence_time(member.verified_at, member.expires_at)
        with self._sessions.begin() as session:
            group = session.get(ChatDB, group_id)
            if group is None or group.deleted_at is not None or group.chat_type not in {"group", "supergroup"}:
                return False
            if group.chat_id != member.group_telegram_id:
                msg = "Membership evidence does not match the group identity generation"
                raise ValueError(msg)
            state = session.get(GroupMembershipStateDB, group_id)
            if state is None:
                state = GroupMembershipStateDB(group_id=group_id, policy_revision=0)
                session.add(state)
            if state.bot_verified_at is not None and member.verified_at <= evidence_time(state.bot_verified_at):
                return False
            self._apply_bot_state(group, state, evidence)
            effective_actor = actor or ActorContext.system("membership verification")
            self._append_audit(
                session,
                effective_actor,
                "membership",
                str(group_id),
                "verify_bot",
                {"group_id": group_id, "bot_status": member.status.value, "policy_revision": state.policy_revision},
            )
            return True

    def _apply_bot_state(self, group: ChatDB, state: GroupMembershipStateDB, evidence: BotCapabilityEvidence) -> None:
        member = evidence.membership
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

    def save_admins(
        self,
        group_id: int,
        evidence: AdministratorEvidence,
        actor: ActorContext | None = None,
    ) -> bool:
        """Persist administrator snapshot and tombstone departed members with audit."""
        self._require_owner()
        validate_evidence_time(evidence.verified_at, evidence.expires_at)
        with self._sessions.begin() as session:
            group = session.get(ChatDB, group_id)
            if group is None or group.deleted_at is not None or group.chat_type not in {"group", "supergroup"}:
                return False
            if group.chat_id != evidence.group_telegram_id:
                msg = "Membership evidence does not match the group identity generation"
                raise ValueError(msg)
            self._validate_admin_snapshot(group, evidence)
            state = session.get(GroupMembershipStateDB, group_id)
            if state is None:
                state = GroupMembershipStateDB(group_id=group_id, policy_revision=0)
                session.add(state)
            if state.admins_verified_at is not None and evidence.verified_at <= evidence_time(state.admins_verified_at):
                return False
            current_ids = self._replace_admin_candidates(session, group_id, evidence)
            if evidence.known:
                for member in evidence.members:
                    self._save_member_row(session, group, member)
            state.admins_known = evidence.known
            state.admins_verified_at, state.admins_expires_at = evidence.verified_at, evidence.expires_at
            state.policy_revision += 1
            effective_actor = actor or ActorContext.system("membership verification")
            self._append_audit(
                session,
                effective_actor,
                "membership",
                str(group_id),
                "verify_admins",
                {
                    "group_id": group_id,
                    "admins_known": evidence.known,
                    "admin_count": len(current_ids),
                    "policy_revision": state.policy_revision,
                },
            )
            return True

    def _validate_admin_snapshot(self, group: ChatDB, evidence: AdministratorEvidence) -> None:
        for member in evidence.members:
            validate_evidence_time(member.verified_at, member.expires_at)
            if member.group_telegram_id != group.chat_id or member.status != MembershipStatus.ADMIN:
                msg = "Administrator snapshot contains mismatched or nonadministrator evidence"
                raise ValueError(msg)

    def _replace_admin_candidates(self, session: Session, group_id: int, evidence: AdministratorEvidence) -> set[int]:
        prior = list(
            session.scalars(
                select(MemberEvidenceDB).where(
                    MemberEvidenceDB.group_id == group_id,
                    MemberEvidenceDB.deleted_at.is_(None),
                    or_(MemberEvidenceDB.admin_candidate.is_(True), MemberEvidenceDB.needs_verification.is_(True)),
                )
            ).all()
        )
        current_ids = {member.user_telegram_id for member in evidence.members} if evidence.known else set()
        for row in prior:
            user = session.get(UserDB, row.user_id)
            if user is None or user.deleted_at is not None or evidence.verified_at <= evidence_time(row.verified_at):
                continue
            if not evidence.known or user.telegram_id not in current_ids:
                row.status = MembershipStatus.UNKNOWN.value
                row.verified_at = evidence.verified_at
                row.expires_at = evidence.expires_at if not evidence.known else evidence.verified_at
                row.needs_verification = True
                if evidence.known:
                    row.admin_candidate = False
        return current_ids

    def save_member(
        self,
        group_id: int,
        evidence: MembershipEvidence,
        actor: ActorContext | None = None,
        *,
        expected_user_id: int | None = None,
    ) -> bool:
        """Persist a single user's membership evidence with audited state mutation."""
        self._require_owner()
        validate_evidence_time(evidence.verified_at, evidence.expires_at)
        with self._sessions.begin() as session:
            group = session.get(ChatDB, group_id)
            if group is None or group.deleted_at is not None or group.chat_type not in {"group", "supergroup"}:
                return False
            if group.chat_id != evidence.group_telegram_id:
                msg = "Membership evidence does not match the group identity generation"
                raise ValueError(msg)
            if not self._save_member_row(session, group, evidence, expected_user_id):
                return False
            state = session.get(GroupMembershipStateDB, group_id)
            if state is None:
                state = GroupMembershipStateDB(group_id=group_id, policy_revision=0)
                session.add(state)
            state.policy_revision += 1
            effective_actor = actor or ActorContext.system("membership verification")
            self._append_audit(
                session,
                effective_actor,
                "membership",
                str(group_id),
                "verify_member",
                {
                    "group_id": group_id,
                    "user_telegram_id": evidence.user_telegram_id,
                    "status": evidence.status.value,
                    "policy_revision": state.policy_revision,
                },
            )
            return True

    def _save_member_row(
        self,
        session: Session,
        group: ChatDB,
        evidence: MembershipEvidence,
        expected_user_id: int | None = None,
    ) -> bool:
        user = session.scalar(
            select(UserDB).where(
                UserDB.telegram_id == evidence.user_telegram_id,
                UserDB.platform == group.platform,
                UserDB.deleted_at.is_(None),
            )
        )
        if user is None and evidence.status == MembershipStatus.ADMIN:
            user = UserDB(telegram_id=evidence.user_telegram_id, platform=group.platform, role=Role.USER)
            session.add(user)
            session.flush()
        if user is None or (expected_user_id is not None and user.id != expected_user_id):
            return False
        if not self._is_new_member_evidence(session, group, user, evidence):
            return False
        row = session.scalar(
            select(MemberEvidenceDB).where(
                MemberEvidenceDB.group_id == group.id,
                MemberEvidenceDB.user_id == user.id,
                MemberEvidenceDB.deleted_at.is_(None),
            )
        )
        if row is None and evidence.status != MembershipStatus.ADMIN:
            return False
        if row is not None and evidence.verified_at <= evidence_time(row.verified_at):
            return False
        self._apply_member_evidence(session, group, user, row, evidence)
        return True

    def _is_new_member_evidence(
        self, session: Session, group: ChatDB, user: UserDB, evidence: MembershipEvidence
    ) -> bool:
        latest = session.scalar(
            select(MemberEvidenceDB)
            .where(MemberEvidenceDB.group_id == group.id, MemberEvidenceDB.user_id == user.id)
            .order_by(MemberEvidenceDB.verified_at.desc(), MemberEvidenceDB.id.desc())
            .limit(1)
        )
        if latest is not None and evidence.verified_at <= evidence_time(latest.verified_at):
            return False
        return not (user.generation > 1 and evidence.verified_at < evidence_time(user.created_at))

    def _apply_member_evidence(
        self,
        session: Session,
        group: ChatDB,
        user: UserDB,
        row: MemberEvidenceDB | None,
        evidence: MembershipEvidence,
    ) -> None:
        if row is None:
            row = MemberEvidenceDB(
                group_id=group.id,
                user_id=user.id,
                status=evidence.status.value,
                verified_at=evidence.verified_at,
                expires_at=evidence.expires_at,
            )
            session.add(row)
        row.status = evidence.status.value
        row.verified_at = evidence.verified_at
        row.expires_at = evidence.expires_at
        if evidence.status != MembershipStatus.UNKNOWN:
            row.admin_candidate = evidence.status == MembershipStatus.ADMIN
            row.needs_verification = False
            if not row.admin_candidate:
                row.deleted_at = evidence.verified_at

    def _append_audit(
        self,
        session: Session,
        actor: ActorContext,
        target_type: str,
        target_id: str,
        action: str,
        changes: dict[str, object],
    ) -> None:
        validate_audit_changes(target_type, action, changes)
        session.add(
            AuditEventDB(
                actor_origin=actor.origin,
                actor_id=actor.actor_id,
                target_type=target_type,
                target_id=target_id,
                action=action,
                changed_fields=changes,
                reason=actor.reason,
                correlation_id=actor.correlation_id,
            )
        )

    def unauthorized_group_chats(self) -> list[ChatDB]:
        """Fetch active unauthorized group/supergroup chats."""
        self._require_owner()
        stmt = (
            select(ChatDB)
            .where(
                ChatDB.status == ChatStatus.UNAUTHORIZED,
                ChatDB.chat_id < 0,
                ChatDB.deleted_at.is_(None),
            )
            .order_by(ChatDB.id)
        )
        with self._sessions() as session:
            return list(session.scalars(stmt).all())

    def pending_chats(self) -> list[ChatDB]:
        """Fetch active pending approval chats."""
        self._require_owner()
        stmt = (
            select(ChatDB)
            .where(
                ChatDB.status == ChatStatus.PENDING,
                ChatDB.deleted_at.is_(None),
            )
            .order_by(ChatDB.id)
        )
        with self._sessions() as session:
            return list(session.scalars(stmt).all())

    def list_owners(self) -> list[UserDB]:
        """Fetch active owner accounts for operator notifications."""
        self._require_owner()
        stmt = (
            select(UserDB)
            .where(
                UserDB.platform == Platform.TELEGRAM,
                UserDB.role == Role.OWNER,
                UserDB.deleted_at.is_(None),
            )
            .order_by(UserDB.telegram_id)
        )
        with self._sessions() as session:
            return list(session.scalars(stmt).all())

    def get_last_notified_version(self) -> str | None:
        """Read last notified version from system state."""
        self._require_owner()
        stmt = select(SystemStateDB.value).where(SystemStateDB.key == "last_notified_version")
        with self._sessions() as session:
            return session.scalar(stmt)

    def set_last_notified_version(self, version: str, actor: ActorContext | None = None) -> None:
        """Update last notified version in system state with audit event."""
        self._require_owner()
        with self._sessions.begin() as session:
            stmt = select(SystemStateDB).where(SystemStateDB.key == "last_notified_version")
            state = session.scalar(stmt)
            if state is not None:
                state.value = version
            else:
                session.add(SystemStateDB(key="last_notified_version", value=version))
            effective_actor = actor or ActorContext.system("set_last_notified_version")
            self._append_audit(
                session,
                effective_actor,
                "system_setting",
                "last_notified_version",
                "set_value",
                {"last_notified_version": version},
            )

    def approved_group_chats(self) -> list[ChatDB]:
        """Fetch active approved Telegram group/supergroup chats."""
        self._require_owner()
        stmt = (
            select(ChatDB)
            .where(
                ChatDB.status == ChatStatus.APPROVED,
                ChatDB.platform == Platform.TELEGRAM,
                ChatDB.chat_id < 0,
                ChatDB.deleted_at.is_(None),
            )
            .order_by(ChatDB.id)
        )
        with self._sessions() as session:
            return list(session.scalars(stmt).all())

    def get_chat(self, chat_id: int) -> ChatDB | None:
        """Fetch an active Telegram chat by its chat_id."""
        self._require_owner()
        stmt = select(ChatDB).where(
            ChatDB.chat_id == chat_id,
            ChatDB.platform == Platform.TELEGRAM,
            ChatDB.deleted_at.is_(None),
        )
        with self._sessions() as session:
            return session.scalar(stmt)
