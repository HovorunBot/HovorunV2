"""Real Casbin checks, current database facts and protected-scope isolation."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest
from dishka import AsyncContainer

from hovorunv2.application.permissions import (
    DecisionReason,
    GroupAdminEvidence,
    PermissionFacts,
    PermissionRequest,
    PermissionTarget,
)
from hovorunv2.application.ports import AuthorizationPolicy, ChatStore, UserStore
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, Role
from hovorunv2.infrastructure.authorization.casbin_policy import CasbinAuthorizationPolicy, load_authorization_policy


@pytest.mark.asyncio
async def test_policy_uses_tiers_and_denies_unknown_actions() -> None:
    """Owner inherits admin permissions without receiving unrestricted action access."""
    policy = await load_authorization_policy()
    for role in Role:
        assert policy.is_allowed(PermissionFacts(role, "bot:administer", PermissionTarget("bot"))) is (
            role >= Role.ADMIN
        )
        assert policy.is_allowed(PermissionFacts(role, "guessed:action", PermissionTarget("bot"))) is False
        assert policy.is_allowed(PermissionFacts(role, "bot:administer", PermissionTarget("group"))) is False


@pytest.mark.asyncio
async def test_service_observes_role_changes_without_policy_reload(test_container: AsyncContainer) -> None:
    """One shared policy never caches user assignments or resurrects deleted authority."""
    access = await test_container.get(AccessService)
    roles = await test_container.get(RoleService)
    users = await test_container.get(UserStore)
    policy = await test_container.get(AuthorizationPolicy)
    request = PermissionRequest(101, "bot:administer", PermissionTarget("bot"), transport_kind="private")
    assert (await access.evaluate_permission(request)).reason == DecisionReason.ENTITY_UNAVAILABLE
    await roles.set_role(101, Role.OWNER, actor=ActorContext.cli())
    assert (await access.evaluate_permission(request)).allowed is True
    assert (await access.evaluate_permission(replace(request, transport_kind="group"))).allowed is False
    assert (await access.evaluate_permission(replace(request, transport_kind=None))).allowed is False
    await roles.set_role(101, Role.USER, actor=ActorContext.cli())
    assert (await access.evaluate_permission(request)).reason == DecisionReason.UNAUTHORIZED_MANAGEMENT
    assert await test_container.get(AuthorizationPolicy) is policy
    await users.soft_delete_user(101, actor=ActorContext.cli(), reason="retired")
    assert (await access.evaluate_permission(request)).reason == DecisionReason.ENTITY_UNAVAILABLE


@pytest.mark.asyncio
async def test_group_evidence_is_actor_target_generation_and_time_scoped(test_container: AsyncContainer) -> None:
    """Evidence cannot authorize another actor, selected group, generation or time window."""
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    access = await test_container.get(AccessService)
    await users.ingest_user(101)
    await users.ingest_user(102)
    chat = await chats.get_or_create_chat(-100101)
    other = await chats.get_or_create_chat(-100102)
    target = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
    other_target = PermissionTarget("group", other.id, other.generation, other.chat_id)
    now = datetime.now(UTC)
    evidence = GroupAdminEvidence(101, chat.id, chat.generation, now - timedelta(minutes=1), now + timedelta(minutes=1))
    request = PermissionRequest(101, "group:settings", target, group_admin=evidence)
    assert (await access.evaluate_permission(request)).allowed is True
    denied = [
        replace(request, actor_id=102),
        replace(request, target=other_target),
        replace(request, target=replace(target, generation=target.generation + 1)),
        replace(request, group_admin=None),
        replace(request, group_admin=replace(evidence, target_generation=evidence.target_generation + 1)),
        replace(request, group_admin=replace(evidence, expires_at=now - timedelta(seconds=1))),
        replace(request, group_admin=replace(evidence, verified_at=now + timedelta(seconds=30))),
        replace(request, group_admin=replace(evidence, verified_at=now.replace(tzinfo=None))),
    ]
    decisions = await asyncio.gather(*(access.evaluate_permission(item) for item in [request, *denied] * 3))
    assert [decision.allowed for decision in decisions] == [True, *([False] * len(denied))] * 3
    assert (await access.evaluate_permission(request)).allowed is True


@pytest.mark.asyncio
async def test_owner_cannot_bypass_group_blockers(test_container: AsyncContainer) -> None:
    """Bans, missing identity and stale target generations precede owner authority."""
    roles = await test_container.get(RoleService)
    chats = await test_container.get(ChatStore)
    statuses = await test_container.get(ChatStatusService)
    access = await test_container.get(AccessService)
    await roles.set_role(101, Role.OWNER, actor=ActorContext.cli())
    chat = await chats.get_or_create_chat(-100101)
    target = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
    request = PermissionRequest(101, "group:settings", target)
    assert (await access.evaluate_permission(request)).allowed is True
    await statuses.set_status(chat.chat_id, ChatStatus.BANNED)
    assert (await access.evaluate_permission(request)).reason == DecisionReason.GROUP_BANNED
    await chats.soft_delete_chat(chat.chat_id, actor=ActorContext.cli(), reason="retired")
    assert (await access.evaluate_permission(request)).reason == DecisionReason.ENTITY_UNAVAILABLE


@pytest.mark.asyncio
async def test_policy_extends_by_data_without_changing_application_port(test_container: AsyncContainer) -> None:
    """A test-only action can be added to the same adapter without a role/action switch."""
    assets = files("hovorunv2.infrastructure.authorization").joinpath("policies")
    model, policy = await asyncio.gather(
        asyncio.to_thread(assets.joinpath("model.conf").read_text, encoding="utf-8"),
        asyncio.to_thread(assets.joinpath("policy.csv").read_text, encoding="utf-8"),
    )
    extended: AuthorizationPolicy = CasbinAuthorizationPolicy(model, policy + "\np, user, test:inspect, bot, role\n")
    facts = PermissionFacts(Role.USER, "test:inspect", PermissionTarget("bot"))
    assert extended.is_allowed(facts) is True
    baseline = await test_container.get(AuthorizationPolicy)
    assert baseline.is_allowed(facts) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "policy"),
    [
        ("not a model", "p, admin, bot:administer, bot, role"),
        (None, "p, admin, incomplete"),
        (None, "p, unknown, bot:administer, bot, role"),
        (None, "p, admin, bot:administer, bot, unsupported"),
        (None, ""),
    ],
)
async def test_invalid_policy_fails_initialization(model: str | None, policy: str) -> None:
    """Malformed assets fail startup rather than producing an available permissive evaluator."""
    if model is None:
        asset = files("hovorunv2.infrastructure.authorization").joinpath("policies/model.conf")
        model = await asyncio.to_thread(asset.read_text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"(?i)authorization"):
        CasbinAuthorizationPolicy(model, policy)


@pytest.mark.asyncio
async def test_legacy_gates_now_share_database_authority(test_container: AsyncContainer) -> None:
    """Legacy role gates use Casbin and database roles after the explicit cutover."""
    access = await test_container.get(AccessService)
    roles = await test_container.get(RoleService)
    await roles.set_role(101, Role.USER, actor=ActorContext.cli())
    legacy = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)
    assert await access.is_allowed(101, 101, legacy) is False
    request = PermissionRequest(101, "bot:administer", PermissionTarget("bot"), transport_kind="private")
    assert (await access.evaluate_permission(request)).reason == DecisionReason.UNAUTHORIZED_MANAGEMENT
    await roles.set_role(101, Role.ADMIN, actor=ActorContext.cli())
    assert await access.is_allowed(101, 101, legacy) is True
    assert (await access.evaluate_permission(request)).allowed is True
