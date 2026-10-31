"""Session-bound current facts; usable by native sessions and SQLAlchemy's async bridge."""

from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from hovorunv2.application.access.permission_policy import authority_allowed, permission_decision
from hovorunv2.application.access.permissions import (
    AccessDecision,
    GroupAdminEvidence,
    PermissionRequest,
    PermissionTarget,
)
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.features.beta import BetaEligibilityFacts
from hovorunv2.application.features.emergency import FeatureEmergencyState, resolve_emergency_key
from hovorunv2.application.features.feature_policy import FeatureFacts
from hovorunv2.application.features.registry import FeatureChannel, FeatureDefinition, FeatureScope, TriggerKind
from hovorunv2.application.groups.group_policy import GroupAccessFacts, group_access
from hovorunv2.application.identity.membership import MembershipStatus
from hovorunv2.application.usage.policies import UsagePolicy, UsageSubject, UsageUnit, UsageWindow
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.emergency import FeatureEmergencyDB
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.domain.group_beta import GroupBetaFeatureDB
from hovorunv2.domain.group_grant import GroupApprovalDB
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.domain.usage import UsageOverrideDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.repositories.policy_queries import (
    active_chat,
    active_user,
    banned_admin_queries,
    current_member,
    fresh_admins,
    group_support,
)


class PolicyFactReader:
    """Read explicit statements, never lazy ORM relationships or historical role assignments."""

    def __init__(self, session: Session, policy: AuthorizationPolicy) -> None:
        self.session = session
        self.policy = policy

    def banned(self, kind: TargetKind, telegram_id: int, *, full: bool = False) -> bool:
        """Personal restrictions affect direct actors; group blockers require full bans.

        Args:
            kind: Target kind of the subject.
            telegram_id: Numeric Telegram ID of the subject.
            full: Whether to require a full ban.

        Returns:
            True if an active, matching ban exists.
        """
        statement = select(BanDB.id).where(
            BanDB.platform == "telegram",
            BanDB.target_kind == kind,
            BanDB.telegram_id == telegram_id,
            BanDB.deleted_at.is_(None),
            BanDB.revoked_at.is_(None),
        )
        if full:
            statement = statement.where(BanDB.kind == BanKind.FULL)
        return self.session.scalar(statement.limit(1)) is not None

    def permission(self, request: PermissionRequest, now: datetime) -> AccessDecision:
        """Revalidate exact group identity and generation before applying shared authority rules.

        Args:
            request: Permission request being evaluated.
            now: Evaluation timestamp.

        Returns:
            Access decision computed from current facts.
        """
        user = self.session.scalar(active_user(request.actor_id, request.platform.value))
        target_active, group_banned = True, False
        if request.target.kind == "group":
            chat = self.session.scalar(active_chat(request.target.telegram_id or 0, request.platform.value))
            target_active = bool(
                chat is not None
                and chat.id == request.target.entity_id
                and chat.generation == request.target.generation
            )
            group_banned = chat is not None and (
                chat.status == ChatStatus.BANNED or self.banned(TargetKind.GROUP, chat.chat_id, full=True)
            )
        return permission_decision(
            request,
            user.role if user is not None else None,
            self.policy,
            now,
            target_active=target_active,
            group_banned=group_banned,
        )

    def _eligible(self, entity: UserDB | ChatDB, definition: FeatureDefinition) -> bool:
        """Return whether the entity holds a permitting beta entitlement."""
        subject = BetaEntitlementDB.user_id if isinstance(entity, UserDB) else BetaEntitlementDB.group_id
        grants = set(
            self.session.scalars(
                select(BetaEntitlementDB.feature).where(
                    subject == entity.id,
                    BetaEntitlementDB.feature.in_((definition.key, ALL_BETA)),
                    BetaEntitlementDB.revoked_at.is_(None),
                    BetaEntitlementDB.deleted_at.is_(None),
                )
            )
        )
        return BetaEligibilityFacts(
            definition.key,
            subject_active=True,
            all_beta=ALL_BETA in grants,
            specific_feature=definition.key in grants,
        ).permits(definition)

    def _selected(self, definition: FeatureDefinition, chat_id: int) -> bool:
        """Return whether the feature is selected for the chat, including legacy capture."""
        chat = self.session.scalar(active_chat(chat_id))
        if chat is None:
            return False
        names = {command.name for command in chat.commands}
        return definition.key in names or (
            not names
            and chat.legacy_empty_capture
            and definition.channel == FeatureChannel.STANDARD
            and definition.provider is not None
            and definition.is_video is not None
        )

    def _manager(self, actor_id: int | None, chat: ChatDB, now: datetime) -> bool:
        """Return whether the actor holds current group settings authority."""
        if actor_id is None:
            return False
        user = self.session.scalar(active_user(actor_id))
        if user is None or self.banned(TargetKind.USER, actor_id):
            return False
        target = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
        member = self.session.scalar(current_member(chat.id, user.id))
        evidence = None
        if (
            self.session.scalar(select(fresh_admins(chat.id, now)))
            and member is not None
            and member.status == MembershipStatus.ADMIN
        ):
            evidence = GroupAdminEvidence(actor_id, chat.id, chat.generation, member.verified_at, member.expires_at)
        return bool(
            self.permission(
                PermissionRequest(
                    actor_id,
                    "group:settings",
                    target,
                    group_admin=evidence,
                    transport_kind="group",
                ),
                now,
            )
        )

    def group_access(self, chat: ChatDB, now: datetime) -> AccessDecision:
        """Read full current candidates and approvals; no bounded maintenance-page shortcut.

        Args:
            chat: Group whose access is evaluated.
            now: Evaluation timestamp.

        Returns:
            Access decision reflecting current candidates and approvals.
        """
        active, unknown = banned_admin_queries(chat.id, now)
        approvals = list(
            self.session.scalars(
                select(GroupApprovalDB).where(
                    GroupApprovalDB.group_id == chat.id,
                    GroupApprovalDB.deleted_at.is_(None),
                )
            )
        )
        approved = any(row.revoked_at is None for row in approvals) or (
            not any(row.revoked_at is not None for row in approvals) and chat.status == ChatStatus.APPROVED
        )
        return group_access(
            GroupAccessFacts(
                active=chat.deleted_at is None,
                banned=chat.status == ChatStatus.BANNED or self.banned(TargetKind.GROUP, chat.chat_id, full=True),
                admins_fresh=bool(self.session.scalar(select(fresh_admins(chat.id, now)))),
                banned_admin=self.session.scalar(active) is not None,
                unknown_admin=self.session.scalar(unknown) is not None,
                approved=approved,
            )
        )

    def _capable(self, chat: ChatDB, required: frozenset[str], now: datetime) -> bool:
        """Return whether the chat's bot state satisfies every required capability."""
        if not required:
            return True
        state = self.session.scalar(
            select(GroupMembershipStateDB).where(
                GroupMembershipStateDB.group_id == chat.id,
                GroupMembershipStateDB.deleted_at.is_(None),
                GroupMembershipStateDB.bot_verified_at <= now,
                GroupMembershipStateDB.bot_expires_at > now,
                GroupMembershipStateDB.bot_status.in_((MembershipStatus.ADMIN, MembershipStatus.MEMBER)),
            )
        )
        if state is None:
            return False
        rights = {
            "administrator": state.bot_status == MembershipStatus.ADMIN,
            "can_delete_messages": state.can_delete_messages,
            "can_restrict_members": state.can_restrict_members,
            "can_invite_users": state.can_invite_users,
            "can_pin_messages": state.can_pin_messages,
            "can_manage_chat": state.can_manage_chat,
        }
        return all(rights.get(right) is True for right in required)

    def feature_facts(
        self,
        definition: FeatureDefinition,
        chat_id: int,
        actor_id: int | None,
        trigger: TriggerKind,
        *,
        configure: bool,
        now: datetime,
    ) -> FeatureFacts:
        """Keep automatic group authority independent of optional human attribution.

        Args:
            definition: Feature definition being evaluated.
            chat_id: Numeric chat ID of the target.
            actor_id: Optional numeric Telegram ID of the actor.
            trigger: Trigger kind of the evaluation.
            configure: Whether this is a configuration action.
            now: Evaluation timestamp.

        Returns:
            Feature facts for the private or group scope.
        """
        scope = FeatureScope.GROUP if chat_id < 0 else FeatureScope.PRIVATE
        actor = self.session.scalar(active_user(actor_id)) if actor_id is not None else None
        automatic = scope == FeatureScope.GROUP and trigger == TriggerKind.AUTOMATIC and not configure
        restricted = not automatic and actor_id is not None and self.banned(TargetKind.USER, actor_id)
        emergency = self._emergency(definition.key)
        if scope == FeatureScope.PRIVATE:
            return FeatureFacts(
                scope,
                target_active=actor is not None and actor_id == chat_id,
                actor_active=actor is not None,
                actor_restricted=restricted,
                private_access=actor is not None
                and (actor.is_trusted or authority_allowed(self.policy, actor.role, "bot:administer")),
                eligible=actor is not None and self._eligible(actor, definition),
                selected=self._selected(definition, chat_id),
                capable=not definition.required_bot_capabilities,
                emergency_disabled=emergency.disabled,
                emergency_epoch=emergency.epoch,
            )
        return self._group_feature_facts(
            definition,
            chat_id,
            actor_id,
            actor,
            restricted=restricted,
            configure=configure,
            now=now,
            emergency=emergency,
        )

    def _emergency(self, feature_key: str) -> FeatureEmergencyState:
        """Read the provider-family emergency state; absence means enabled at epoch zero."""
        key = resolve_emergency_key(feature_key)
        row = self.session.scalar(select(FeatureEmergencyDB).where(FeatureEmergencyDB.feature_key == key))
        if row is None:
            return FeatureEmergencyState(key)
        return FeatureEmergencyState(row.feature_key, row.disabled, row.reason, row.public_reason, row.epoch)

    def _group_feature_facts(
        self,
        definition: FeatureDefinition,
        chat_id: int,
        actor_id: int | None,
        actor: UserDB | None,
        *,
        restricted: bool,
        configure: bool,
        now: datetime,
        emergency: FeatureEmergencyState,
    ) -> FeatureFacts:
        """Read group selection, support, access and capability facts in order."""
        chat = self.session.scalar(active_chat(chat_id))
        if chat is None:
            return FeatureFacts(FeatureScope.GROUP)
        supported, eligible = True, True
        selected = self._selected(definition, chat_id)
        if definition.channel == FeatureChannel.BETA:
            selected, supported = self._group_beta(chat, definition, chat_id, now)
            eligible = supported or (configure and actor is not None and self._eligible(actor, definition))
        return FeatureFacts(
            FeatureScope.GROUP,
            target_active=True,
            actor_active=actor is not None,
            actor_restricted=restricted,
            manager=self._manager(actor_id, chat, now),
            access_reason=self.group_access(chat, now).reason,
            eligible=eligible,
            selected=selected,
            supported=supported,
            capable=self._capable(chat, definition.required_bot_capabilities, now),
            emergency_disabled=emergency.disabled,
            emergency_epoch=emergency.epoch,
        )

    def _group_beta(
        self, chat: ChatDB, definition: FeatureDefinition, chat_id: int, now: datetime
    ) -> tuple[bool, bool]:
        """Return group beta selection and support facts for the canonical route."""
        # Entitlements require the canonical numeric route; aliases do not copy group grants.
        if chat.chat_id != chat_id or chat.chat_type not in {"group", "supergroup"}:
            return False, False
        selection = self.session.scalar(
            select(GroupBetaFeatureDB).where(
                GroupBetaFeatureDB.group_id == chat.id,
                GroupBetaFeatureDB.feature == definition.key,
                GroupBetaFeatureDB.deleted_at.is_(None),
            )
        )
        granted = bool(
            self.session.scalar(
                select(
                    exists().where(
                        BetaEntitlementDB.group_id == chat.id,
                        BetaEntitlementDB.feature == definition.key,
                        BetaEntitlementDB.deleted_at.is_(None),
                        BetaEntitlementDB.revoked_at.is_(None),
                    )
                )
            )
        )
        return bool(selection and selection.selected), granted or (
            selection is not None and self.session.scalar(group_support(selection, now)) is not None
        )

    def chat_translation_settings(self, chat_id: int) -> tuple[str | None, tuple[str, ...] | None] | None:
        """Read current canonical preferences without copying profiles or writing any state.

        Args:
            chat_id: Numeric chat ID of the chat.

        Returns:
            A pair of target language and ignored languages, or None if absent or noncanonical.
        """
        chat = self.session.scalar(active_chat(chat_id))
        if chat is None or chat.chat_id != chat_id:
            return None
        ignored = tuple(chat.ignored_langs) if chat.ignored_langs is not None else None
        return chat.target_lang, ignored

    def usage_subject(
        self, actor_id: int | None, group_id: int | None, trigger: TriggerKind
    ) -> tuple[UsageSubject, bool, bool]:
        """Resolve charged identity separately from the execution target and optional actor.

        Args:
            actor_id: Optional numeric Telegram ID of the direct actor.
            group_id: Optional numeric group ID for automatic triggers.
            trigger: Trigger kind of the usage.

        Returns:
            A triple of the charged subject, trust flag and bypass flag.

        Raises:
            ValueError: If the automatic group or direct user is unavailable.
        """
        if trigger == TriggerKind.AUTOMATIC:
            chat = self.session.scalar(active_chat(group_id or 0))
            if chat is None or chat.chat_id != group_id or chat.chat_type not in {"group", "supergroup"}:
                msg = "Automatic usage requires an active canonical group."
                raise ValueError(msg)
            return UsageSubject(TargetKind.GROUP, chat.chat_id, chat.id, chat.generation), False, False
        if actor_id is None or actor_id <= 0:
            msg = "Direct usage requires an active user."
            raise ValueError(msg)
        user = self.session.scalar(active_user(actor_id))
        if user is None:
            msg = "Direct usage requires an active user."
            raise ValueError(msg)
        return (
            UsageSubject(TargetKind.USER, actor_id, user.id, user.generation),
            user.is_trusted,
            authority_allowed(self.policy, user.role, "bot:administer"),
        )

    def usage_overrides(self, subject: UsageSubject) -> tuple[UsagePolicy, ...]:
        """Read only overrides belonging to the just-resolved active lifecycle.

        Args:
            subject: Charged usage subject.

        Returns:
            Active usage overrides for the subject.
        """
        column = UsageOverrideDB.user_id if subject.kind == TargetKind.USER else UsageOverrideDB.group_id
        return tuple(
            UsagePolicy(
                row.bucket,
                row.limit,
                row.burst,
                row.concurrency,
                row.pending,
                UsageUnit(row.unit),
                UsageWindow(row.window_seconds),
            )
            for row in self.session.scalars(
                select(UsageOverrideDB)
                .where(
                    column == subject.entity_id,
                    UsageOverrideDB.deleted_at.is_(None),
                )
                .order_by(UsageOverrideDB.id)
            )
        )
