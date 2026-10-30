"""Durable group activation with real SQLite, current authority and transactional audit."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hovorunv2.application.activation import ActivationAction, ActivationOutcome, ActivationRequest, ActivationStore
from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import DecisionReason, GroupAdminEvidence, PermissionTarget
from hovorunv2.application.ports import AuthorizationPolicy, ChatStore, UserStore
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.base import Base
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, ChatStatus, CommandName, Role, TargetKind
from hovorunv2.domain.group_grant import ApprovalBasis, GroupAccessRequestDB, GroupApprovalDB, RequestStatus
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.infrastructure.persistence.activation_store import SQLAlchemyActivationStore
from hovorunv2.infrastructure.persistence.audited_transaction import SQLAlchemyAuditedTransaction
from tests.conftest import provision_role, verify_group_admins
from tests.utils.activation import ACTOR, ADMIN, GROUP, activation_runtime, seed_activation, stub_telegram
from tests.utils.postgres import reject_audit
from tests.utils.recorded_responses import RecordedResponses


async def test_trusted_activation_is_durable_idempotent_and_audited(test_container: AsyncContainer) -> None:
    """A trusted non-admin needs no membership API; retries preserve settings and provenance."""
    target = await seed_activation(test_container)
    store = await test_container.get(ActivationStore)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses():
            assert (await runtime.service.enable(ACTOR, target, "supergroup")).outcome == ActivationOutcome.APPROVED
    commands = await test_container.get(CommandService)
    await commands.disable_command(GROUP, CommandName.TIKTOK_VIDEO)
    before = await commands.get_allowed_commands(GROUP)
    assert (
        await store.execute(ActivationRequest(ACTOR, target, "group"))
    ).outcome == ActivationOutcome.ALREADY_APPROVED
    assert await commands.get_allowed_commands(GROUP) == before
    users = await test_container.get(UserStore)
    await users.set_trust(ACTOR, is_trusted=False, actor=ActorContext.cli())
    await users.soft_delete_user(ACTOR, ActorContext.cli(), "Retired synthetic identity")
    await verify_group_admins(test_container, GROUP)
    statuses = await test_container.get(ChatStatusService)
    assert (await statuses.evaluate_access(GROUP)).allowed
    grants = await test_container.get(GroupGrantStore)
    assert len(await grants.approvals(target.entity_id)) == 1
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        events = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_activation"))).all()
        )
        assert len(events) == 1
        assert events[0].actor_id == ACTOR
        assert events[0].actor_origin == ActorOrigin.TELEGRAM
        assert events[0].changed_fields["activation_basis"] == "trust"
        assert events[0].changed_fields["defaults_added"]
        assert events[0].changed_fields["actor_user_id"] is not None


@pytest.mark.parametrize("status", ["creator", "member", "left"])
async def test_only_untrusted_group_administrator_can_request(test_container: AsyncContainer, status: str) -> None:
    """Fresh creator evidence can request; ordinary members and departed users cannot."""
    target = await seed_activation(test_container, trusted=False)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http, member_status=status)
            first = await runtime.service.enable(ACTOR, target, "supergroup")
            second = await runtime.service.enable(ACTOR, target, "supergroup")
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        requests = list((await session.scalars(select(GroupAccessRequestDB))).all())
        assert not list((await session.scalars(select(GroupApprovalDB))).all())
    if status == "creator":
        assert first.outcome == ActivationOutcome.REQUESTED
        assert second.outcome == ActivationOutcome.ALREADY_PENDING
        assert first.request_id == second.request_id
        assert len(requests) == 1
    else:
        assert first.outcome == second.outcome == ActivationOutcome.DENIED
        assert not requests


@pytest.mark.parametrize("change", ["trust", "personal", "full", "deleted", "group_ban", "group_deleted"])
async def test_mutation_rechecks_current_facts_after_menu_open(test_container: AsyncContainer, change: str) -> None:
    """A selected target or old trust snapshot never authorizes a later mutation."""
    target = await seed_activation(test_container)
    users = await test_container.get(UserStore)
    if change == "trust":
        await users.set_trust(ACTOR, is_trusted=False, actor=ActorContext.cli())
    elif change == "deleted":
        await users.soft_delete_user(ACTOR, ActorContext.cli(), "Retired")
    elif change == "group_deleted":
        chats = await test_container.get(ChatStore)
        await chats.soft_delete_chat(GROUP, ActorContext.cli(), "Retired")
    else:
        bans = await test_container.get(BanStore)
        await bans.set_ban(
            TargetKind.GROUP if change == "group_ban" else TargetKind.USER,
            GROUP if change == "group_ban" else ACTOR,
            BanKind.PERSONAL if change == "personal" else BanKind.FULL,
            active=True,
            actor=ActorContext.cli(reason="Synthetic restriction"),
        )
    store = await test_container.get(ActivationStore)
    result = await store.execute(ActivationRequest(ACTOR, target, "group"))
    assert result.outcome in {ActivationOutcome.DENIED, ActivationOutcome.UNAVAILABLE, ActivationOutcome.GROUP_BANNED}
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert not list((await session.scalars(select(GroupApprovalDB))).all())
        assert not list((await session.scalars(select(GroupAccessRequestDB))).all())
        assert not list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_activation"))).all()
        )


@pytest.mark.parametrize("invalid", ["generation", "identity", "transport", "unknown"])
async def test_selected_target_and_transport_are_required(test_container: AsyncContainer, invalid: str) -> None:
    """Never provision a target, select another group, or activate through private transport."""
    target = await seed_activation(test_container)
    transport = "private" if invalid == "transport" else "group"
    if invalid == "generation":
        target = replace(target, generation=target.generation + 1)
    elif invalid == "identity":
        target = replace(target, entity_id=target.entity_id + 1)
    elif invalid == "unknown":
        target = PermissionTarget("group", 9999, 1, -9999)
    store = await test_container.get(ActivationStore)
    assert (await store.execute(ActivationRequest(ACTOR, target, transport))).outcome in {
        ActivationOutcome.DENIED,
        ActivationOutcome.UNAVAILABLE,
    }


async def test_reapproval_and_empty_config_preserve_selections(test_container: AsyncContainer) -> None:
    """History and explicit empty configuration prevent default replay, including regrant."""
    target = await seed_activation(test_container)
    commands = await test_container.get(CommandService)
    await commands.enable_command(GROUP, CommandName.TIKTOK_VIDEO)
    await commands.disable_command(GROUP, CommandName.TIKTOK_VIDEO)
    store = await test_container.get(ActivationStore)
    assert (await store.execute(ActivationRequest(ACTOR, target, "group"))).outcome == ActivationOutcome.APPROVED
    assert await commands.get_allowed_commands(GROUP) == set()
    grants = await test_container.get(GroupGrantStore)
    await grants.revoke_approval(target.entity_id, ApprovalBasis.EXPLICIT, ActorContext.cli())
    assert (await store.execute(ActivationRequest(ACTOR, target, "group"))).outcome == ActivationOutcome.APPROVED
    assert await commands.get_allowed_commands(GROUP) == set()


async def test_actor_full_ban_only_suspends_while_current_admin(test_container: AsyncContainer) -> None:
    """Durable approval survives an actor ban and later demotion; ordinary membership never suspends."""
    target = await seed_activation(test_container)
    store = await test_container.get(ActivationStore)
    await store.execute(ActivationRequest(ACTOR, target, "group"))
    await verify_group_admins(test_container, GROUP)
    bans = await test_container.get(BanStore)
    await bans.set_ban(TargetKind.USER, ACTOR, BanKind.FULL, active=True, actor=ActorContext.cli(reason="Synthetic"))
    statuses = await test_container.get(ChatStatusService)
    assert (await statuses.evaluate_access(GROUP)).allowed
    memberships = await test_container.get(MembershipStore)
    now = datetime.now(UTC)
    await memberships.save_member(
        target.entity_id,
        MembershipEvidence(
            GROUP, ACTOR, MembershipStatus.ADMIN, now - timedelta(minutes=1), now + timedelta(minutes=4)
        ),
        ActorContext.cli(),
    )
    assert (await statuses.evaluate_access(GROUP)).reason == DecisionReason.ADMIN_FULL_BANNED
    await memberships.save_member(
        target.entity_id,
        MembershipEvidence(
            GROUP, ACTOR, MembershipStatus.MEMBER, now - timedelta(seconds=1), now + timedelta(minutes=4)
        ),
        ActorContext.cli(),
    )
    assert (await statuses.evaluate_access(GROUP)).allowed
    assert (await store.execute(ActivationRequest(ACTOR, target, "group"))).outcome == ActivationOutcome.DENIED
    assert (await statuses.evaluate_access(GROUP)).allowed


@pytest.mark.parametrize("approved", [True, False])
async def test_bot_administrator_decides_pending_request_in_private(
    test_container: AsyncContainer, approved: bool
) -> None:
    """Bot ADMIN can approve/reject; replay and demotion cannot reuse a pending decision."""
    target = await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ADMIN)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            pending = await runtime.service.enable(ACTOR, target, "group")
        action = ActivationAction.APPROVE if approved else ActivationAction.REJECT
        result = await runtime.service.decide(ADMIN, target, pending.request_id, action, "private")
        assert result.outcome == (ActivationOutcome.APPROVED if approved else ActivationOutcome.REJECTED)
        assert (
            await runtime.service.decide(ADMIN, target, pending.request_id, action, "private")
        ).outcome == ActivationOutcome.STALE_REQUEST
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        row = await session.get(GroupAccessRequestDB, pending.request_id)
        assert row.status == (RequestStatus.APPROVED if approved else RequestStatus.REJECTED)
        assert row.decision_actor_id == ADMIN
        approvals = list((await session.scalars(select(GroupApprovalDB))).all())
        assert bool(approvals) == approved


async def test_open_admin_decision_rechecks_demotion_and_target(test_container: AsyncContainer) -> None:
    """A stale admin menu, wrong request, or mismatched target cannot approve."""
    target = await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ADMIN)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            pending = await runtime.service.enable(ACTOR, target, "group")
        roles = await test_container.get(RoleService)
        await roles.set_role(ADMIN, Role.USER, ActorContext.cli())
        assert (
            await runtime.service.decide(ADMIN, target, pending.request_id, ActivationAction.APPROVE, "private")
        ).outcome == ActivationOutcome.DENIED
        await roles.set_role(ADMIN, Role.ADMIN, ActorContext.cli())
        assert (
            await runtime.service.decide(ADMIN, target, pending.request_id + 1, ActivationAction.APPROVE, "private")
        ).outcome == ActivationOutcome.STALE_REQUEST
        assert (
            await runtime.service.decide(
                ADMIN, replace(target, generation=2), pending.request_id, ActivationAction.APPROVE, "private"
            )
        ).outcome == ActivationOutcome.UNAVAILABLE
        assert (
            await runtime.service.decide(ADMIN, target, pending.request_id, ActivationAction.APPROVE, "group")
        ).outcome == ActivationOutcome.DENIED


@pytest.mark.fresh_schema
async def test_audit_failure_rolls_back_approval_defaults_status_and_request(test_container: AsyncContainer) -> None:
    """A failing audit insert leaves all activation effects, including pending decisions, unchanged."""
    target = await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ADMIN)
    sessions = await test_container.get(async_sessionmaker)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            pending = await runtime.service.enable(ACTOR, target, "group")
        async with sessions() as session:
            await reject_audit(
                session,
                "fail_activation",
                "NEW.target_type = 'group_activation' AND NEW.action = 'approve'",
                "audit unavailable",
            )
            await session.commit()
        with pytest.raises(IntegrityError, match="audit unavailable"):
            await runtime.service.decide(ADMIN, target, pending.request_id, ActivationAction.APPROVE, "private")
    async with sessions() as session:
        assert not list((await session.scalars(select(GroupApprovalDB))).all())
        assert (await session.get(GroupAccessRequestDB, pending.request_id)).status == RequestStatus.PENDING
        assert (await session.get(ChatDB, target.entity_id)).status == ChatStatus.PENDING
    commands = await test_container.get(CommandService)
    assert not await commands.get_allowed_commands(GROUP)


@pytest.mark.parametrize("scope", ["expired", "future", "other_actor", "other_group"])
async def test_request_evidence_is_actor_target_and_time_scoped(test_container: AsyncContainer, scope: str) -> None:
    """Old or unrelated administrator evidence cannot submit an untrusted request."""
    target = await seed_activation(test_container, trusted=False)
    now = datetime.now(UTC)
    evidence = GroupAdminEvidence(
        ACTOR + 1 if scope == "other_actor" else ACTOR,
        target.entity_id + 1 if scope == "other_group" else target.entity_id,
        target.generation,
        now + timedelta(minutes=1) if scope == "future" else now - timedelta(minutes=1),
        now - timedelta(seconds=1) if scope == "expired" else now + timedelta(minutes=5),
    )
    store = await test_container.get(ActivationStore)
    assert (
        await store.execute(ActivationRequest(ACTOR, target, "group", group_admin=evidence))
    ).outcome == ActivationOutcome.DENIED


@pytest.mark.parametrize("trusted", [True, False])
async def test_concurrent_activation_is_idempotent_on_real_postgres_connections(
    test_container: AsyncContainer, test_engine: AsyncEngine, trusted: bool
) -> None:
    """Independent connections serialize checks and create only one approval/request/audit."""
    engine = test_engine
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await setup_fixtures(sessions)
        async with sessions() as session:
            group = ChatDB(chat_id=GROUP, chat_type="supergroup")
            session.add_all([group, UserDB(telegram_id=ACTOR, is_trusted=trusted)])
            await session.commit()
            target = PermissionTarget("group", group.id, group.generation, GROUP)
        now = datetime.now(UTC)
        evidence = GroupAdminEvidence(ACTOR, target.entity_id, target.generation, now, now + timedelta(minutes=5))
        request = ActivationRequest(ACTOR, target, "group", group_admin=evidence)
        policy = await test_container.get(AuthorizationPolicy)
        store = SQLAlchemyActivationStore(SQLAlchemyAuditedTransaction(sessions), policy)
        results = await asyncio.gather(store.execute(request), store.execute(request))
        assert {result.outcome for result in results} == (
            {ActivationOutcome.APPROVED, ActivationOutcome.ALREADY_APPROVED}
            if trusted
            else {ActivationOutcome.REQUESTED, ActivationOutcome.ALREADY_PENDING}
        )
        async with sessions() as session:
            events = list(
                (
                    await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_activation"))
                ).all()
            )
            approvals = list((await session.scalars(select(GroupApprovalDB))).all())
            requests = list((await session.scalars(select(GroupAccessRequestDB))).all())
            assert len(events) == 1
            assert len(approvals) == int(trusted)
            assert len(requests) == int(not trusted)
    finally:
        await engine.dispose()
