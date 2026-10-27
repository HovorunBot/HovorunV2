"""Whole-group beta support, current authority and atomic audit on real SQLite."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.beta import BetaEntitlementStore
from hovorunv2.application.features import (
    FEATURE_REGISTRY,
    FeatureChannel,
    FeatureDefinition,
    FeatureRegistry,
    FeatureScope,
)
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.group_migration import GroupMigrationStore
from hovorunv2.application.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.application.ports import ChatStore, CommandStore, UserStore
from hovorunv2.application.services.beta_service import BetaEligibilityService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.group_beta_service import GroupBetaService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Role, TargetKind
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.user import UserDB
from tests.conftest import provision_role

GROUP = -100290001
FIRST = 290001
SECOND = 290002
MEMBER = 290003
BOT_ADMIN = 290010
BETA = FeatureDefinition("synthetic_group_beta", "Synthetic group beta", channel=FeatureChannel.BETA)
FUTURE = FeatureDefinition("later_group_beta", "Later group beta", channel=FeatureChannel.BETA, introduction="later")
PRIVATE = FeatureDefinition(
    "private_beta", "Private beta", channel=FeatureChannel.BETA, scopes=frozenset({FeatureScope.PRIVATE})
)
GROUP_ONLY = FeatureDefinition(
    "group_only_beta", "Group-only beta", channel=FeatureChannel.BETA, scopes=frozenset({FeatureScope.GROUP})
)
REGISTRY = FeatureRegistry((*FEATURE_REGISTRY.all(), BETA, FUTURE, PRIVATE, GROUP_ONLY))
REJECT_AUDIT = (
    "CREATE TRIGGER reject_group_beta BEFORE INSERT ON audit_events "
    "WHEN NEW.target_type = 'group_beta' "
    "BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END"
)


@dataclass(frozen=True)
class GroupBetaRuntime:
    """Real services and exact lifecycle identities for one synthetic approved group."""

    service: GroupBetaService
    eligibility: BetaEligibilityService
    target: PermissionTarget
    first: PermissionTarget
    second: PermissionTarget


async def user_target(container: AsyncContainer, telegram_id: int) -> PermissionTarget:
    """Create a synthetic identity through the actual ingestion store."""
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    user = await users.get_by_telegram_id(telegram_id)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, user.telegram_id)


async def group_beta_runtime(container: AsyncContainer) -> GroupBetaRuntime:
    """Seed approval and fresh administrator evidence without internal service doubles."""
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(GROUP)
    await chats.update_status(GROUP, ChatStatus.APPROVED)
    grants = await container.get(GroupGrantStore)
    await grants.approve(chat.id, ActorContext.cli())
    first = await user_target(container, FIRST)
    second = await user_target(container, SECOND)
    await user_target(container, MEMBER)
    target = PermissionTarget("group", chat.id, chat.generation, GROUP)
    now = datetime.now(UTC) - timedelta(minutes=1)
    memberships = await container.get(MembershipStore)
    members = tuple(
        MembershipEvidence(GROUP, user_id, MembershipStatus.ADMIN, now, now + timedelta(minutes=5))
        for user_id in (FIRST, SECOND)
    )
    await memberships.save_admins(
        chat.id, AdministratorEvidence(GROUP, True, members, now, now + timedelta(minutes=5)), ActorContext.cli()
    )
    service = GroupBetaService(await container.get(GroupBetaStore), REGISTRY, await container.get(ChatStatusService))
    eligibility = BetaEligibilityService(await container.get(BetaEntitlementStore), REGISTRY)
    await eligibility.set_grant(first, ALL_BETA, granted=True, actor=ActorContext.cli())
    await eligibility.set_grant(second, BETA.key, granted=True, actor=ActorContext.cli())
    return GroupBetaRuntime(service, eligibility, target, first, second)


async def enable_local(runtime: GroupBetaRuntime, actor_id: int = FIRST) -> bool:
    """Exercise the same public application operation an authenticated group admin uses."""
    return await runtime.service.set_enabled(
        runtime.target, BETA.key, enabled=True, actor=ActorContext.telegram(actor_id), transport_kind="group"
    )


async def test_multiple_enablers_suspend_and_recover_only_the_selected_beta(test_container: AsyncContainer) -> None:
    """Members use group beta without personal grants; the final enabler controls suspension only."""
    runtime = await group_beta_runtime(test_container)
    commands = await test_container.get(CommandStore)
    standard = FEATURE_REGISTRY.all()[0].key
    await commands.enable_command(GROUP, standard, actor=ActorContext.cli())
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert await enable_local(runtime)
    assert not await enable_local(runtime)
    assert await enable_local(runtime, SECOND)
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    member = await user_target(test_container, MEMBER)
    assert not await runtime.eligibility.is_eligible(member, BETA.key)
    assert not await runtime.service.is_available(runtime.target, FUTURE.key, emergency_disabled=False)
    assert await runtime.eligibility.set_grant(runtime.first, ALL_BETA, granted=False, actor=ActorContext.cli())
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert await runtime.eligibility.set_grant(runtime.second, BETA.key, granted=False, actor=ActorContext.cli())
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert (await runtime.service.facts(runtime.target, BETA.key)).selected
    grants = await test_container.get(GroupGrantStore)
    assert await grants.approvals(runtime.target.entity_id)
    assert await commands.get_allowed_commands(GROUP) == [standard]
    assert await runtime.eligibility.set_grant(runtime.first, ALL_BETA, granted=True, actor=ActorContext.cli())
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(GroupBetaEnablerDB)) == 2
        events = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_beta"))).all()
        )
        assert len(events) == 2
        assert all(event.actor_id in {FIRST, SECOND} for event in events)


async def test_explicit_group_grant_needs_activation_and_respects_independent_blockers(
    test_container: AsyncContainer,
) -> None:
    """Global authority supplies neither silent eligibility nor exemptions from group or emergency guards."""
    runtime = await group_beta_runtime(test_container)
    await provision_role(test_container, BOT_ADMIN)
    actor = ActorContext.telegram(BOT_ADMIN)
    with pytest.raises(PermissionError):
        await runtime.service.set_enabled(runtime.target, BETA.key, enabled=True, actor=actor, transport_kind="private")
    assert await runtime.eligibility.set_grant(
        runtime.target, BETA.key, granted=True, actor=actor, transport_kind="private"
    )
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert await runtime.service.set_enabled(
        runtime.target, BETA.key, enabled=True, actor=actor, transport_kind="private"
    )
    await runtime.eligibility.set_grant(runtime.first, ALL_BETA, granted=False, actor=ActorContext.cli())
    await runtime.eligibility.set_grant(runtime.second, BETA.key, granted=False, actor=ActorContext.cli())
    facts = await runtime.service.facts(runtime.target, BETA.key)
    assert facts.group_granted
    assert not facts.qualifying_enabler
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=True)
    bans = await test_container.get(BanStore)
    await bans.set_ban(TargetKind.GROUP, GROUP, BanKind.FULL, active=True, actor=ActorContext.cli(reason="Synthetic"))
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    await bans.set_ban(TargetKind.GROUP, GROUP, BanKind.FULL, active=False, actor=ActorContext.cli(reason="Synthetic"))
    await bans.set_ban(TargetKind.USER, FIRST, BanKind.FULL, active=True, actor=ActorContext.cli(reason="Synthetic"))
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    await bans.set_ban(TargetKind.USER, FIRST, BanKind.FULL, active=False, actor=ActorContext.cli(reason="Synthetic"))
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert await runtime.eligibility.set_grant(runtime.target, BETA.key, granted=False, actor=ActorContext.cli())
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    assert (await runtime.service.facts(runtime.target, BETA.key)).selected


@pytest.mark.parametrize(
    "invalid",
    [
        "member",
        "expired",
        "future",
        "unknown",
        "deleted",
        "needs_verification",
        "snapshot_unknown",
        "snapshot_expired",
        "snapshot_future",
        "snapshot_deleted",
    ],
)
async def test_activation_rechecks_current_membership_instead_of_replayed_authority(
    test_container: AsyncContainer, invalid: str
) -> None:
    """Trust, entitlement and an old admin action cannot replace current verified group authority."""
    runtime = await group_beta_runtime(test_container)
    users = await test_container.get(UserStore)
    await users.set_trust(FIRST, is_trusted=True, actor=ActorContext.cli())
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        evidence = (
            await session.scalars(select(MemberEvidenceDB).where(MemberEvidenceDB.user_id == runtime.first.entity_id))
        ).one()
        now = datetime.now(UTC)
        if invalid.startswith("snapshot_"):
            state = await session.get(GroupMembershipStateDB, runtime.target.entity_id)
            assert state is not None
            if invalid == "snapshot_unknown":
                state.admins_known = False
            elif invalid == "snapshot_expired":
                state.admins_expires_at = now - timedelta(seconds=1)
            elif invalid == "snapshot_future":
                state.admins_verified_at = now + timedelta(minutes=1)
            else:
                state.deleted_at = now
        elif invalid in {"member", "unknown"}:
            evidence.status = invalid
        elif invalid == "expired":
            evidence.expires_at = now - timedelta(seconds=1)
        elif invalid == "future":
            evidence.verified_at = now + timedelta(minutes=1)
        elif invalid == "deleted":
            evidence.deleted_at = now
        else:
            evidence.needs_verification = True
    with pytest.raises(PermissionError):
        await enable_local(runtime)
    assert not (await runtime.service.facts(runtime.target, BETA.key)).selected


async def test_eligible_regular_member_and_private_local_admin_cannot_replay_activation(
    test_container: AsyncContainer,
) -> None:
    """Possessing feature eligibility or another actor's intended action grants no group administration."""
    runtime = await group_beta_runtime(test_container)
    member = await user_target(test_container, MEMBER)
    await runtime.eligibility.set_grant(member, ALL_BETA, granted=True, actor=ActorContext.cli())
    with pytest.raises(PermissionError):
        await enable_local(runtime, MEMBER)
    with pytest.raises(PermissionError):
        await runtime.service.set_enabled(
            runtime.target, BETA.key, enabled=True, actor=ActorContext.telegram(FIRST), transport_kind="private"
        )
    with pytest.raises(PermissionError):
        await runtime.service.set_enabled(runtime.target, BETA.key, enabled=True, actor=ActorContext.system())


async def test_disable_clears_support_and_reenable_does_not_reuse_old_enablers(test_container: AsyncContainer) -> None:
    """Disabling is explicit and auditable; old supporting rows remain revoked history."""
    runtime = await group_beta_runtime(test_container)
    await enable_local(runtime)
    await enable_local(runtime, SECOND)
    await runtime.eligibility.set_grant(runtime.first, ALL_BETA, granted=False, actor=ActorContext.cli())
    assert await runtime.service.set_enabled(
        runtime.target, BETA.key, enabled=False, actor=ActorContext.telegram(FIRST), transport_kind="group"
    )
    assert not await runtime.service.set_enabled(
        runtime.target, BETA.key, enabled=False, actor=ActorContext.telegram(FIRST), transport_kind="group"
    )
    assert not (await runtime.service.facts(runtime.target, BETA.key)).selected
    with pytest.raises(PermissionError):
        await enable_local(runtime)
    assert await enable_local(runtime, SECOND)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        enablers = list((await session.scalars(select(GroupBetaEnablerDB))).all())
        assert len(enablers) == 3
        assert sum(row.revoked_at is None for row in enablers) == 1
        assert next(row for row in enablers if row.revoked_at is None).user_id == runtime.second.entity_id


@pytest.mark.parametrize("lost", ["user", "entitlement", "enabler", "selection", "group"])
async def test_deleted_lifecycle_and_support_records_never_supply_availability(
    test_container: AsyncContainer, lost: str
) -> None:
    """Current reads filter each soft-deletable source without resetting configuration elsewhere."""
    runtime = await group_beta_runtime(test_container)
    await enable_local(runtime)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        if lost == "user":
            row = await session.get(UserDB, runtime.first.entity_id)
        elif lost == "entitlement":
            row = (
                await session.scalars(
                    select(BetaEntitlementDB).where(BetaEntitlementDB.user_id == runtime.first.entity_id)
                )
            ).one()
        elif lost == "enabler":
            row = (await session.scalars(select(GroupBetaEnablerDB))).one()
        elif lost == "selection":
            row = (await session.scalars(select(GroupBetaFeatureDB))).one()
        else:
            row = await session.get(ChatDB, runtime.target.entity_id)
        assert row is not None
        row.deleted_at = datetime.now(UTC)
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)


async def test_expired_enabler_suspends_and_fresh_admin_evidence_recovers(test_container: AsyncContainer) -> None:
    """Selected state survives temporary membership uncertainty and expiry."""
    runtime = await group_beta_runtime(test_container)
    await enable_local(runtime)
    future = datetime.now(UTC) + timedelta(minutes=10)
    assert not (await runtime.service.facts(runtime.target, BETA.key, now=future)).qualifying_enabler
    memberships = await test_container.get(MembershipStore)
    now = datetime.now(UTC)
    await memberships.save_member(
        runtime.target.entity_id,
        MembershipEvidence(GROUP, FIRST, MembershipStatus.MEMBER, now, now + timedelta(minutes=5)),
        ActorContext.cli(),
    )
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    await memberships.save_member(
        runtime.target.entity_id,
        MembershipEvidence(
            GROUP, FIRST, MembershipStatus.ADMIN, now + timedelta(microseconds=1), now + timedelta(minutes=5)
        ),
        ActorContext.cli(),
    )
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)


async def test_exact_target_binding_and_bot_admin_demotion_are_rechecked(test_container: AsyncContainer) -> None:
    """Neither a retired Telegram route nor stale generation can mutate preserved internal selection."""
    runtime = await group_beta_runtime(test_container)
    stale = replace(runtime.target, generation=runtime.target.generation + 1)
    assert not (await runtime.service.facts(stale, BETA.key)).subject_active
    with pytest.raises(ValueError, match="known active group identity"):
        await runtime.service.set_enabled(stale, BETA.key, enabled=True, actor=ActorContext.cli())
    await enable_local(runtime)
    migration = await test_container.get(GroupMigrationStore)
    new_route = -100290099
    assert await migration.migrate(GROUP, new_route)
    assert not (await runtime.service.facts(runtime.target, BETA.key)).subject_active
    with pytest.raises(ValueError, match="known active group identity"):
        await enable_local(runtime)
    current = replace(runtime.target, telegram_id=new_route)
    assert (await runtime.service.facts(current, BETA.key)).selected
    assert not (await runtime.service.facts(current, BETA.key)).qualifying_enabler
    with pytest.raises(PermissionError):
        await runtime.service.set_enabled(
            current, BETA.key, enabled=True, actor=ActorContext.telegram(FIRST), transport_kind="supergroup"
        )
    now = datetime.now(UTC)
    members = tuple(
        MembershipEvidence(new_route, user_id, MembershipStatus.ADMIN, now, now + timedelta(minutes=5))
        for user_id in (FIRST, SECOND)
    )
    memberships = await test_container.get(MembershipStore)
    await memberships.save_admins(
        current.entity_id,
        AdministratorEvidence(new_route, True, members, now, now + timedelta(minutes=5)),
        ActorContext.cli(),
    )
    assert await runtime.service.is_available(current, BETA.key, emergency_disabled=False)
    await provision_role(test_container, BOT_ADMIN)
    await runtime.eligibility.set_grant(current, BETA.key, granted=True, actor=ActorContext.cli())
    roles = await test_container.get(RoleService)
    await roles.set_role(BOT_ADMIN, Role.USER, actor=ActorContext.cli())
    with pytest.raises(PermissionError):
        await runtime.service.set_enabled(
            current, BETA.key, enabled=True, actor=ActorContext.telegram(BOT_ADMIN), transport_kind="private"
        )


async def test_audit_failure_rolls_back_selection_and_enabler_enable_and_disable(
    test_container: AsyncContainer,
) -> None:
    """The audited transaction cannot leave selected state or enablers changed after insert failure."""
    runtime = await group_beta_runtime(test_container)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await session.execute(text(REJECT_AUDIT))
    with pytest.raises(IntegrityError):
        await enable_local(runtime)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(GroupBetaFeatureDB)) == 0
        assert await session.scalar(select(func.count()).select_from(GroupBetaEnablerDB)) == 0
        await session.execute(text("DROP TRIGGER reject_group_beta"))
    await enable_local(runtime)
    async with sessions.begin() as session:
        await session.execute(text(REJECT_AUDIT))
    with pytest.raises(IntegrityError):
        await runtime.service.set_enabled(
            runtime.target, BETA.key, enabled=False, actor=ActorContext.telegram(FIRST), transport_kind="group"
        )
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    async with sessions() as session:
        assert (await session.scalars(select(GroupBetaEnablerDB))).one().revoked_at is None
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.target_type == "group_beta")
            )
            == 1
        )


async def test_registry_scope_and_feature_bound_facts_fail_closed(test_container: AsyncContainer) -> None:
    """Unknown, standard and private-only features cannot become group beta selections."""
    runtime = await group_beta_runtime(test_container)
    for feature in ("unknown", FEATURE_REGISTRY.all()[0].key, PRIVATE.key):
        with pytest.raises(
            ValueError, match=r"Unknown feature|requires a beta feature|requires a group-scoped feature"
        ):
            await runtime.service.set_enabled(runtime.target, feature, enabled=True, actor=ActorContext.cli())
    with pytest.raises(ValueError, match="requires a group-scoped feature"):
        await runtime.service.facts(runtime.first, BETA.key)
    await enable_local(runtime)
    facts = await runtime.service.facts(runtime.target, BETA.key)
    assert not facts.permits(FUTURE, group_allowed=True, emergency_disabled=False)
    assert not facts.permits(BETA, group_allowed=False, emergency_disabled=False)
    assert not facts.permits(BETA, group_allowed=True, emergency_disabled=True)


async def test_personal_ban_blocks_mutations_without_revoking_group_owned_support(
    test_container: AsyncContainer,
) -> None:
    """A personal restriction affects human commands; only a full-banned admin suspends the group."""
    runtime = await group_beta_runtime(test_container)
    await enable_local(runtime)
    bans = await test_container.get(BanStore)
    await bans.set_ban(
        TargetKind.USER, FIRST, BanKind.PERSONAL, active=True, actor=ActorContext.cli(reason="Synthetic")
    )
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    with pytest.raises(PermissionError, match="Restricted actors"):
        await runtime.service.set_enabled(
            runtime.target, BETA.key, enabled=False, actor=ActorContext.telegram(FIRST), transport_kind="group"
        )
    await bans.set_ban(TargetKind.USER, FIRST, BanKind.FULL, active=True, actor=ActorContext.cli(reason="Synthetic"))
    assert not await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)


async def test_group_only_beta_accepts_specific_user_eligibility_for_local_activation(
    test_container: AsyncContainer,
) -> None:
    """User eligibility can support group activation without implying private feature execution."""
    runtime = await group_beta_runtime(test_container)
    assert not await runtime.eligibility.is_eligible(runtime.second, GROUP_ONLY.key)
    assert await runtime.eligibility.set_grant(runtime.second, GROUP_ONLY.key, granted=True, actor=ActorContext.cli())
    assert await runtime.eligibility.is_eligible(runtime.second, GROUP_ONLY.key)
    assert not await runtime.service.is_available(runtime.target, GROUP_ONLY.key, emergency_disabled=False)
    assert await runtime.service.set_enabled(
        runtime.target, GROUP_ONLY.key, enabled=True, actor=ActorContext.telegram(SECOND), transport_kind="group"
    )
    assert await runtime.service.is_available(runtime.target, GROUP_ONLY.key, emergency_disabled=False)
    with pytest.raises(ValueError, match="target scope"):
        await runtime.eligibility.set_grant(runtime.target, PRIVATE.key, granted=True, actor=ActorContext.cli())


async def test_cli_override_needs_group_grant_and_never_fabricates_enabler(test_container: AsyncContainer) -> None:
    """CLI activation uses explicit group eligibility independently of local administrators."""
    runtime = await group_beta_runtime(test_container)
    with pytest.raises(PermissionError, match="explicit group grant"):
        await runtime.service.set_enabled(runtime.target, BETA.key, enabled=True, actor=ActorContext.cli())
    await runtime.eligibility.set_grant(runtime.target, BETA.key, granted=True, actor=ActorContext.cli())
    assert await runtime.service.set_enabled(runtime.target, BETA.key, enabled=True, actor=ActorContext.cli())
    assert await runtime.service.is_available(runtime.target, BETA.key, emergency_disabled=False)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(GroupBetaEnablerDB)) == 0
