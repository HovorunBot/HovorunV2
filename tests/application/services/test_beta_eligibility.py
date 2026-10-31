"""Real PostgreSQL eligibility, current authority, lifecycle and atomic audit regressions."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.features.beta import BetaEntitlementStore
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureChannel, FeatureDefinition, FeatureRegistry
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB, chat_commands
from hovorunv2.domain.enums import Role
from tests.conftest import provision_role
from tests.utils.postgres import reject_audit

BETA = FeatureDefinition("synthetic_beta", "Synthetic beta", channel=FeatureChannel.BETA)
FUTURE_BETA = FeatureDefinition(
    "future_beta", "Later synthetic beta", channel=FeatureChannel.BETA, introduction="later"
)
REGISTRY = FeatureRegistry((*FEATURE_REGISTRY.all(), BETA))


async def beta_user(container: AsyncContainer, telegram_id: int) -> PermissionTarget:
    """Create a synthetic baseline user and return exact lifecycle identity."""
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    user = await users.get_by_telegram_id(telegram_id)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, user.telegram_id)


async def test_all_beta_covers_future_definitions_without_activation(test_container: AsyncContainer) -> None:
    """All-beta is dynamic; trust, role and feature selection never serve as implicit grants."""
    target = await beta_user(test_container, 880280)
    store = await test_container.get(BetaEntitlementStore)
    service = BetaEligibilityService(store, REGISTRY)
    users = await test_container.get(UserStore)
    await users.set_trust(880280, is_trusted=True, actor=ActorContext.cli())
    assert not await service.is_eligible(target, BETA.key)
    assert await service.set_grant(target, ALL_BETA, granted=True, actor=ActorContext.cli())
    assert not await service.set_grant(target, ALL_BETA, granted=True, actor=ActorContext.cli())
    assert await service.is_eligible(target, BETA.key)
    expanded = BetaEligibilityService(store, FeatureRegistry((*REGISTRY.all(), FUTURE_BETA)))
    assert await expanded.is_eligible(target, FUTURE_BETA.key)
    assert not FUTURE_BETA.default_enabled
    assert await expanded.set_grant(target, ALL_BETA, granted=False, actor=ActorContext.cli())
    assert not await expanded.is_eligible(target, FUTURE_BETA.key)
    assert not await expanded.is_eligible(target, BETA.key)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        grants = list((await session.scalars(select(BetaEntitlementDB))).all())
        assert len(grants) == 1
        assert grants[0].revoked_at is not None
        assert grants[0].revision == 2
        assert await session.scalar(select(func.count()).select_from(chat_commands)) == 0
        audits = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "beta_entitlement"))).all()
        )
        assert len(audits) == 2
        assert audits[0].changed_fields == {"feature": "*", "granted": [False, True], "generation": 1}


async def test_specific_grants_group_override_and_revocation_preserve_configuration(
    test_container: AsyncContainer,
) -> None:
    """User/group entitlements stay independent and never mutate durable group approval or configuration."""
    target = await beta_user(test_container, 880281)
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(-880281)
    group = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
    commands = await test_container.get(CommandStore)
    selected = FEATURE_REGISTRY.all()[0].key
    await commands.enable_command(-880281, selected, actor=ActorContext.cli())
    before_selection = await commands.get_allowed_commands(-880281)
    store = await test_container.get(BetaEntitlementStore)
    service = BetaEligibilityService(store, FeatureRegistry((*REGISTRY.all(), FUTURE_BETA)))
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        existing = await session.get(ChatDB, chat.id)
        assert existing is not None
        existing.target_lang = "uk"
        existing.encryption_salt = "synthetic-encrypted-state-marker"
        await session.commit()
    assert await service.set_grant(target, BETA.key, granted=True, actor=ActorContext.cli())
    assert await service.is_eligible(target, BETA.key)
    assert not await service.is_eligible(target, FUTURE_BETA.key)
    facts = await service.facts(target, BETA.key)
    assert not facts.permits(FUTURE_BETA)
    assert not await service.is_eligible(group, BETA.key)
    admin = await provision_role(test_container, 880282)
    assert await service.set_grant(
        group, BETA.key, granted=True, actor=ActorContext.telegram(admin), transport_kind="private"
    )
    assert await service.is_eligible(group, BETA.key)
    assert await service.set_grant(group, BETA.key, granted=False, actor=ActorContext.cli())
    assert not await service.is_eligible(group, BETA.key)
    assert await service.is_eligible(target, BETA.key)
    assert await commands.get_allowed_commands(-880281) == before_selection
    async with session_maker() as session:
        unchanged = await session.get(ChatDB, chat.id)
        assert unchanged is not None
        assert unchanged.target_lang == "uk"
        assert unchanged.encryption_salt == "synthetic-encrypted-state-marker"
        assert unchanged.status == chat.status
        assert list(
            (await session.scalars(select(BetaEntitlementDB).where(BetaEntitlementDB.group_id == chat.id))).all()
        )
    with pytest.raises(ValueError, match="only to users"):
        await service.set_grant(group, ALL_BETA, granted=True, actor=ActorContext.cli())
    with pytest.raises(ValueError, match="Unknown feature"):
        await service.set_grant(target, "unknown_beta", granted=True, actor=ActorContext.cli())
    with pytest.raises(ValueError, match="beta feature"):
        await service.set_grant(target, FEATURE_REGISTRY.all()[0].key, granted=True, actor=ActorContext.cli())


async def test_beta_mutation_rechecks_authority_and_exact_lifecycle(test_container: AsyncContainer) -> None:
    """Demotion, untrusted transport, tombstones and stale generations fail at the mutation boundary."""
    target = await beta_user(test_container, 880283)
    admin = await provision_role(test_container, 880284)
    store = await test_container.get(BetaEntitlementStore)
    service = BetaEligibilityService(store, REGISTRY)
    for actor, transport in (
        (ActorContext.system(), None),
        (ActorContext.telegram(880283), "private"),
        (ActorContext.telegram(admin), "group"),
    ):
        with pytest.raises(PermissionError):
            await service.set_grant(target, ALL_BETA, granted=True, actor=actor, transport_kind=transport)
    deleted_admin = await provision_role(test_container, 880287)
    users = await test_container.get(UserStore)
    await users.soft_delete_user(deleted_admin, actor=ActorContext.cli(), reason="synthetic actor deletion")
    with pytest.raises(PermissionError):
        await service.set_grant(
            target, BETA.key, granted=True, actor=ActorContext.telegram(deleted_admin), transport_kind="private"
        )
    roles = await test_container.get(RoleService)
    await roles.set_role(admin, Role.USER, actor=ActorContext.cli())
    with pytest.raises(PermissionError):
        await service.set_grant(
            target, BETA.key, granted=True, actor=ActorContext.telegram(admin), transport_kind="private"
        )
    stale = PermissionTarget("user", target.entity_id, target.generation + 1, target.telegram_id)
    assert not await service.is_eligible(stale, BETA.key)
    with pytest.raises(ValueError, match="known active identity"):
        await service.set_grant(stale, BETA.key, granted=True, actor=ActorContext.cli())
    assert await service.set_grant(target, BETA.key, granted=True, actor=ActorContext.cli())
    users = await test_container.get(UserStore)
    await users.soft_delete_user(880283, actor=ActorContext.cli(), reason="synthetic deletion")
    assert not await service.is_eligible(target, BETA.key)
    with pytest.raises(ValueError, match="known active identity"):
        await service.set_grant(target, BETA.key, granted=False, actor=ActorContext.cli())


@pytest.mark.fresh_schema
async def test_beta_entitlement_audit_failure_rolls_back(test_container: AsyncContainer) -> None:
    """An audit rejection cannot leave a committed grant or a revoked entitlement."""
    target = await beta_user(test_container, 880285)
    store = await test_container.get(BetaEntitlementStore)
    service = BetaEligibilityService(store, REGISTRY)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        await reject_audit(
            session, "reject_beta_audit", "NEW.target_type = 'beta_entitlement'", "synthetic audit failure"
        )
        await session.commit()
    with pytest.raises(IntegrityError):
        await service.set_grant(target, ALL_BETA, granted=True, actor=ActorContext.cli())
    assert not await service.is_eligible(target, BETA.key)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(BetaEntitlementDB)) == 0
        await session.execute(text("DROP TRIGGER reject_beta_audit ON audit_events"))
        await session.commit()
    assert await service.set_grant(target, ALL_BETA, granted=True, actor=ActorContext.cli())
    async with session_maker() as session:
        await reject_audit(
            session, "reject_beta_revoke", "NEW.target_type = 'beta_entitlement'", "synthetic audit failure"
        )
        await session.commit()
    with pytest.raises(IntegrityError):
        await service.set_grant(target, ALL_BETA, granted=False, actor=ActorContext.cli())
    assert await service.is_eligible(target, BETA.key)


async def test_beta_regrant_retains_history_and_filters_deleted_grants_and_groups(
    test_container: AsyncContainer,
) -> None:
    """Revocation/deletion suppress access immediately without erasing rows or inheriting another generation's grant."""
    user = await beta_user(test_container, 880288)
    store = await test_container.get(BetaEntitlementStore)
    service = BetaEligibilityService(store, REGISTRY)
    assert await service.set_grant(user, BETA.key, granted=True, actor=ActorContext.cli())
    assert await service.set_grant(user, BETA.key, granted=False, actor=ActorContext.cli())
    assert not await service.set_grant(user, BETA.key, granted=False, actor=ActorContext.cli())
    assert await service.set_grant(user, BETA.key, granted=True, actor=ActorContext.cli())
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        grants = list((await session.scalars(select(BetaEntitlementDB).order_by(BetaEntitlementDB.id))).all())
        assert len(grants) == 2
        assert grants[0].revoked_at is not None
        assert grants[1].revoked_at is None
        await session.execute(
            text("UPDATE beta_entitlements SET deleted_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL")
        )
        await session.commit()
    assert not await service.is_eligible(user, BETA.key)
    assert await service.set_grant(user, BETA.key, granted=True, actor=ActorContext.cli())
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(-880288)
    group = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
    assert await service.set_grant(group, BETA.key, granted=True, actor=ActorContext.cli())
    await chats.soft_delete_chat(-880288, actor=ActorContext.cli(), reason="synthetic group deletion")
    assert not await service.is_eligible(group, BETA.key)
    with pytest.raises(ValueError, match="known active identity"):
        await service.set_grant(group, BETA.key, granted=False, actor=ActorContext.cli())
