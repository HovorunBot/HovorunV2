"""Bot/native-worker policy parity and accounting against isolated real PostgreSQL."""

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from os import getpid
from pathlib import Path

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import GroupAdminEvidence, PermissionRequest, PermissionTarget
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.features import FEATURE_REGISTRY, FeatureRegistry, TriggerKind
from hovorunv2.application.groups.group_migration import GroupMigrationStore
from hovorunv2.application.ports import ChatStore, CommandStore, UserStore
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.usage_policy_service import UsagePolicyService
from hovorunv2.application.usage import MEDIA_BUCKET, UsagePolicy, UsagePolicyStore, UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import AppliedUsage, UsageLedgerStore
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.beta import BetaEntitlementDB
from hovorunv2.domain.enums import ChatStatus, Role, TargetKind
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.role import UserRoleDB
from hovorunv2.domain.usage_accounting import UsageAccountingEventDB, UsageAggregateDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.config import DatabaseSettings
from tests.utils.identities import ACTOR, SUBJECT, usage_event
from tests.utils.postgres import reject_audit
from tests.utils.runtimes.beta import BETA, FIRST, GROUP, MEMBER, REGISTRY
from tests.utils.runtimes.features import CAPABLE, feature_runtime
from tests.utils.usage import USER, usage_subjects


def database_config(url: str) -> DatabaseSettings:
    """Never read a developer's private environment file in native test composition."""
    return DatabaseSettings(database_url=url, _env_file=None)


async def change_group_state(container: AsyncContainer, bot: FeatureAccessService, state: str) -> None:
    """Apply committed real lifecycle changes before comparing bot/native policy reads."""
    sessions = await container.get(async_sessionmaker)
    now = datetime.now(UTC)
    if state == "unselected":
        await bot.set_group_beta(BETA.key, GROUP, FIRST, enabled=False)
    elif state in {"group_banned", "admin_banned"}:
        kind, target = (TargetKind.GROUP, GROUP) if state == "group_banned" else (TargetKind.USER, FIRST)
        await (await container.get(BanStore)).set_ban(
            kind, target, BanKind.FULL, active=True, actor=ActorContext.cli(reason="synthetic")
        )
    elif state == "deleted_group":
        await (await container.get(ChatStore)).soft_delete_chat(GROUP, actor=ActorContext.cli(), reason="synthetic")
    elif state == "migrated":
        await (await container.get(GroupMigrationStore)).migrate(GROUP, GROUP - 1)
    else:
        await change_group_rows(sessions, state, now)


async def _update_membership_evidence(session: AsyncSession, state: str, now: datetime) -> None:
    for evidence in await session.scalars(select(GroupMembershipStateDB)):
        if state == "expired":
            evidence.admins_expires_at = now - timedelta(seconds=1)
        else:
            evidence.admins_known = False


async def change_group_rows(sessions: async_sessionmaker, state: str, now: datetime) -> None:
    """Exercise stale and deleted facts through committed real rows."""
    async with sessions.begin() as session:
        if state == "grant_revoked":
            for grant in await session.scalars(select(BetaEntitlementDB)):
                grant.revoked_at = now
        elif state in {"expired", "unknown"}:
            await _update_membership_evidence(session, state, now)
        elif state == "deleted_admin":
            for member in await session.scalars(select(MemberEvidenceDB)):
                member.deleted_at = now


@pytest.mark.parametrize(
    ("state", "allowed"),
    [
        ("selected", True),
        ("unselected", False),
        ("grant_revoked", False),
        ("group_banned", False),
        ("admin_banned", False),
        ("expired", False),
        ("unknown", False),
        ("deleted_admin", False),
        ("deleted_group", False),
        ("migrated", False),
    ],
)
async def test_group_policy_matrix(
    test_container: AsyncContainer,
    postgres_database_url: str,
    state: str,
    allowed: bool,
) -> None:
    """Both runtimes preserve selection, beta support, bans, evidence and canonical routing."""
    bot = await feature_runtime(test_container, FeatureRegistry((*REGISTRY.all(), CAPABLE)))
    await bot.set_group_beta(BETA.key, GROUP, FIRST, enabled=True)
    await change_group_state(test_container, bot, state)
    with WorkerDatabase(database_config(postgres_database_url), bot.registry) as worker:
        for trigger in TriggerKind:
            for actor in (MEMBER, FIRST, None):
                actual = await asyncio.to_thread(
                    partial(worker.policies.evaluate, BETA.key, GROUP, actor, trigger=trigger)
                )
                expected = await bot.evaluate(BETA.key, GROUP, actor, trigger=trigger)
                assert actual == expected
                assert actual.allowed is (allowed and (actor is not None or trigger == TriggerKind.AUTOMATIC))
        for actor in (MEMBER, FIRST):
            for key in (BETA.key, CAPABLE.key, "unknown_feature"):
                assert await asyncio.to_thread(
                    partial(worker.policies.evaluate, key, GROUP, actor, configure=True)
                ) == await bot.evaluate(key, GROUP, actor, configure=True)


@pytest.mark.parametrize("state", ["untrusted", "trusted", "admin", "historical_role", "personal_ban", "deleted"])
async def test_private_current_authority_and_trust(
    test_container: AsyncContainer,
    postgres_database_url: str,
    state: str,
) -> None:
    """Trust, role history and bans cannot substitute for current private authority."""
    bot = await test_container.get(FeatureAccessService)
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(USER)
    key = FEATURE_REGISTRY.all()[0].key
    await (await test_container.get(CommandStore)).enable_command(USER, key)
    if state in {"trusted", "personal_ban", "deleted"}:
        await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli(reason="synthetic"))
    if state == "admin":
        await (await test_container.get(RoleService)).set_role(
            USER, Role.ADMIN, actor=ActorContext.cli(reason="synthetic")
        )
    if state == "historical_role":
        sessions = await test_container.get(async_sessionmaker)
        async with sessions.begin() as session:
            user = await session.scalar(select(UserDB).where(UserDB.telegram_id == USER))
            assert user is not None
            session.add(UserRoleDB(user_id=user.id, telegram_id=USER, role=Role.OWNER))
    if state == "personal_ban":
        await (await test_container.get(BanStore)).set_ban(
            TargetKind.USER, USER, BanKind.PERSONAL, active=True, actor=ActorContext.cli(reason="synthetic")
        )
    if state == "deleted":
        await users.soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic")
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        actual = await asyncio.to_thread(worker.policies.evaluate, key, USER, USER)
        assert actual == await bot.evaluate(key, USER, USER)
        assert actual.allowed is (state in {"trusted", "admin"})


async def test_permission_generation_matrix(test_container: AsyncContainer, postgres_database_url: str) -> None:
    """Exact actor/target generation and evidence freshness remain identical across runtimes."""
    bot = await test_container.get(PermissionService)
    users = await test_container.get(UserStore)
    await users.ingest_user(USER)
    chat = await (await test_container.get(ChatStore)).get_or_create_chat(GROUP)
    target = PermissionTarget("group", chat.id, chat.generation, GROUP)
    now = datetime.now(UTC)
    evidence = GroupAdminEvidence(
        USER, chat.id, chat.generation, now - timedelta(minutes=1), now + timedelta(minutes=1)
    )
    request = PermissionRequest(USER, "group:settings", target, group_admin=evidence)
    requests = [
        request,
        replace(request, actor_id=USER + 1),
        replace(request, target=replace(target, generation=2)),
        replace(request, group_admin=replace(evidence, expires_at=now - timedelta(seconds=1))),
        replace(request, group_admin=replace(evidence, target_generation=2)),
        replace(request, target=PermissionTarget("bot"), action="bot:administer", transport_kind="group"),
    ]
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        decisions = [await asyncio.to_thread(worker.policies.permission, item) for item in requests]
        assert decisions == [await bot.evaluate(item) for item in requests]
        assert [decision.allowed for decision in decisions] == [True, False, False, False, False, False]
        await (await test_container.get(RoleService)).set_role(
            USER, Role.OWNER, actor=ActorContext.cli(reason="synthetic")
        )
        await (await test_container.get(ChatStore)).update_status(GROUP, ChatStatus.BANNED)
        assert not (await asyncio.to_thread(worker.policies.permission, request)).allowed
        assert await asyncio.to_thread(worker.policies.permission, request) == await bot.evaluate(request)


async def test_usage_overrides_are_fresh_and_subject_scoped(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Worker sessions see committed trust/role/override changes, never cached admission grants."""
    user, _group = await usage_subjects(test_container)
    bot = await test_container.get(UsagePolicyService)
    key = FEATURE_REGISTRY.all()[0].key
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        for trusted in (False, True):
            await (await test_container.get(UserStore)).set_trust(
                USER, is_trusted=trusted, actor=ActorContext.cli(reason="synthetic")
            )
            for trigger in TriggerKind:
                from_group = user.telegram_id if trigger == TriggerKind.DIRECT else _group.telegram_id
                assert await asyncio.to_thread(
                    partial(worker.policies.resolve, key, trigger, USER, group_id=from_group)
                ) == await bot.resolve(key, trigger, USER, group_id=from_group)
        await (await test_container.get(UsagePolicyStore)).set_override(
            user.target, UsagePolicy(MEDIA_BUCKET, 4, 2, 1, 5), actor=ActorContext.cli(reason="synthetic")
        )
        assert await asyncio.to_thread(worker.policies.resolve, key, TriggerKind.DIRECT, USER) == await bot.resolve(
            key, TriggerKind.DIRECT, USER
        )
        await (await test_container.get(RoleService)).set_role(
            USER, Role.ADMIN, actor=ActorContext.cli(reason="synthetic")
        )
        assert (await asyncio.to_thread(worker.policies.resolve, key, TriggerKind.DIRECT, USER)).bypass_personal_quota


async def native_probe(url: str, event_id: str) -> dict[str, int]:
    """Launch an actual interpreter process; only the PostgreSQL schema is shared."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-W",
        "error",
        str(Path(__file__).parents[1] / "utils" / "native_database_probe.py"),
        url,
        event_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise
    assert process.returncode == 0, stderr.decode()
    assert not stderr, stderr.decode()
    return json.loads(stdout)


@pytest.mark.subprocess
@pytest.mark.slow
async def test_native_processes_share_receipts_with_bot(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Independent native processes claim one receipt and bot replay cannot charge it again."""
    await usage_subjects(test_container)
    results = await asyncio.gather(
        native_probe(postgres_database_url, "native:completion"),
        native_probe(postgres_database_url, "native:completion"),
    )
    assert len({result["pid"] for result in results} | {getpid()}) == 3
    assert sum(result["applied"] for result in results) == 1
    assert {result["total"] for result in results} == {1}
    ledger = await test_container.get(UsageLedgerStore)
    event = replace(usage_event("native:completion"), charges=(usage_event("native:completion").charges[1],))
    assert await ledger.apply_batch((event,), actor=ACTOR) == AppliedUsage()
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UsageAccountingEventDB)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.target_type == "usage_batch")
            )
            == 1
        )


@pytest.mark.fresh_schema
async def test_native_conflict_and_audit_failure_roll_back(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Native mutations retain all-or-nothing receipt, aggregate and audit behavior."""
    await usage_subjects(test_container)
    bot = await test_container.get(UsageLedgerStore)
    await bot.apply_batch((usage_event("stable"),), actor=ACTOR)
    sessions = await test_container.get(async_sessionmaker)
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        with pytest.raises(ValueError, match="different charges"):
            await asyncio.to_thread(
                partial(worker.ledger.apply_batch, (usage_event("a-new"), usage_event("stable", amount=2)), actor=ACTOR)
            )
        assert (
            await asyncio.to_thread(worker.ledger.total, SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90)
            == 1
        )
        async with sessions.begin() as session:
            assert await session.get(UsageAccountingEventDB, "a-new") is None
            await reject_audit(session, "reject_native_audit", "NEW.target_type = 'usage_batch'", "synthetic")
        with pytest.raises(IntegrityError):
            await asyncio.to_thread(partial(worker.ledger.apply_batch, (usage_event("audit-failure"),), actor=ACTOR))
        async with sessions() as session:
            assert await session.get(UsageAccountingEventDB, "audit-failure") is None
            assert await session.scalar(select(func.sum(UsageAggregateDB.amount))) == 2
        saved = worker.policies
    with pytest.raises(ValueError, match="owning process"):
        await asyncio.to_thread(saved.evaluate, FEATURE_REGISTRY.all()[0].key, USER, USER)


async def test_worker_rejects_obsolete_lifecycle_and_route_references(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Deletion/recreation and migration cannot make an old queued reference current again."""
    user, group = await usage_subjects(test_container)
    users = await test_container.get(UserStore)
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        assert await asyncio.to_thread(worker.policies.active_target, user.target)
        assert not await asyncio.to_thread(worker.policies.active_target, replace(user.target, generation=2))
        assert not await asyncio.to_thread(
            worker.policies.active_target, replace(group.target, entity_id=group.entity_id + 1)
        )
        await users.soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic")
        sessions = await test_container.get(async_sessionmaker)
        async with sessions.begin() as session:
            session.add(UserDB(telegram_id=USER, generation=user.generation + 1))
        fresh = await users.get_by_telegram_id(USER)
        assert fresh is not None
        assert not await asyncio.to_thread(worker.policies.active_target, user.target)
        assert await asyncio.to_thread(
            worker.policies.active_target, PermissionTarget("user", fresh.id, fresh.generation, USER)
        )
        await (await test_container.get(GroupMigrationStore)).migrate(group.telegram_id, group.telegram_id - 1)
        assert not await asyncio.to_thread(worker.policies.active_target, group.target)
        assert await asyncio.to_thread(
            worker.policies.active_target, replace(group.target, telegram_id=group.telegram_id - 1)
        )
