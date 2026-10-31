"""Usage policy precedence, charging and audited lifecycle behavior against real SQLite."""

from dataclasses import replace

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureDefinition, FeatureRegistry, TriggerKind
from hovorunv2.application.ports import AuditedUnitOfWork, AuthorizationPolicy, ChatStore, UserStore
from hovorunv2.application.usage.policies import (
    MEDIA_BUCKET,
    UsagePolicy,
    UsagePolicyStore,
    UsageSafetyCaps,
    UsageUnit,
    UsageWindow,
)
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import TargetKind
from hovorunv2.domain.usage import UsageOverrideDB
from hovorunv2.infrastructure.persistence.stores.usage_policy_store import SQLAlchemyUsagePolicyStore
from tests.conftest import provision_role
from tests.utils.postgres import reject_audit
from tests.utils.usage import GROUP, USER, usage_subjects

FEATURE = FEATURE_REGISTRY.all()[0].key
OTHER_FEATURE = FEATURE_REGISTRY.all()[1].key


async def test_direct_and_automatic_defaults_follow_charged_subject(test_container: AsyncContainer) -> None:
    """Direct group commands charge users; automatic work charges the group, independent of activator trust."""
    await usage_subjects(test_container)
    service = await test_container.get(UsagePolicyService)
    direct = await service.resolve(FEATURE, TriggerKind.DIRECT, USER, group_id=GROUP)
    assert direct.subject.kind == TargetKind.USER
    assert direct.subject.telegram_id == USER
    assert {policy.bucket for policy in direct.policies} == {MEDIA_BUCKET, f"feature:{FEATURE}"}
    assert {(policy.limit, policy.burst, policy.concurrency, policy.pending) for policy in direct.policies} == {
        (6, 3, 1, 10)
    }
    users = await test_container.get(UserStore)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    trusted = await service.resolve(FEATURE, TriggerKind.DIRECT, USER)
    assert {(policy.limit, policy.burst, policy.concurrency) for policy in trusted.policies} == {(12, 6, 2)}
    automatic = await service.resolve(FEATURE, TriggerKind.AUTOMATIC, USER, group_id=GROUP)
    assert automatic.subject.kind == TargetKind.GROUP
    assert {(policy.limit, policy.burst, policy.concurrency, policy.pending) for policy in automatic.policies} == {
        (12, 6, 2, 25)
    }
    await users.set_trust(USER, is_trusted=False, actor=ActorContext.cli())
    assert await service.resolve(FEATURE, TriggerKind.AUTOMATIC, USER, group_id=GROUP) == automatic
    with pytest.raises(ValueError, match="canonical group"):
        await service.resolve(FEATURE, TriggerKind.AUTOMATIC, USER)


async def test_overrides_precede_defaults_and_shared_family_covers_provider_switch(
    test_container: AsyncContainer,
) -> None:
    """Individual and family windows intersect; replacing a feature never evades its shared media bucket."""
    user, group = await usage_subjects(test_container)
    store = await test_container.get(UsagePolicyStore)
    service = await test_container.get(UsagePolicyService)
    override = UsagePolicy(MEDIA_BUCKET, 4, 2, 1, 5)
    assert await store.set_override(user.target, override, actor=ActorContext.cli())
    await (await test_container.get(UserStore)).set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    for feature in (FEATURE, OTHER_FEATURE):
        resolved = await service.resolve(feature, TriggerKind.DIRECT, USER)
        assert override in resolved.policies
        assert next(policy for policy in resolved.policies if policy.bucket != MEDIA_BUCKET).limit == 12
    additional = replace(override, limit=100, burst=20, window=UsageWindow(3600))
    assert await store.set_override(user.target, additional, actor=ActorContext.cli())
    assert len((await service.resolve(FEATURE, TriggerKind.DIRECT, USER)).policies) == 3
    assert all(
        policy.limit == 12
        for policy in (await service.resolve(FEATURE, TriggerKind.AUTOMATIC, USER, group_id=GROUP)).policies
    )
    assert await store.set_override(group.target, replace(override, limit=7), actor=ActorContext.cli())
    assert (
        next(
            policy
            for policy in (await service.resolve(FEATURE, TriggerKind.AUTOMATIC, USER, group_id=GROUP)).policies
            if policy.bucket == MEDIA_BUCKET
        ).limit
        == 7
    )


async def test_admin_bypass_preserves_group_limits_and_safety(test_container: AsyncContainer) -> None:
    """Ordinary personal quota bypass never removes global caps or automatic group charging."""
    user, _group = await usage_subjects(test_container)
    await provision_role(test_container, USER)
    store = await test_container.get(UsagePolicyStore)
    await store.set_override(user.target, UsagePolicy(MEDIA_BUCKET, 999, 999, 999, 999), actor=ActorContext.cli())
    service = await test_container.get(UsagePolicyService)
    direct = await service.resolve(FEATURE, TriggerKind.DIRECT, USER)
    automatic = await service.resolve(FEATURE, TriggerKind.AUTOMATIC, USER, group_id=GROUP)
    assert direct.bypass_personal_quota
    assert not automatic.bypass_personal_quota
    assert direct.safety == automatic.safety == UsageSafetyCaps()
    assert direct.safety.global_concurrency == 2
    assert direct.safety.global_pending == 500
    assert direct.safety.payload_bytes == 64 * 1024
    assert direct.safety.total_payload_bytes == 32 * 1024 * 1024


@pytest.mark.parametrize("field", ["limit", "burst", "concurrency", "pending"])
@pytest.mark.parametrize("value", [0, -1])
def test_invalid_policies_never_mean_unlimited(field: str, value: int) -> None:
    """Reject invalid values before durable mutation or admission."""
    with pytest.raises(ValueError, match="positive"):
        replace(UsagePolicy(MEDIA_BUCKET, 6, 3, 1, 10), **{field: value})


@pytest.mark.parametrize("seconds", [0, -60])
def test_invalid_windows_are_explicit(seconds: int) -> None:
    """Zero-duration windows cannot create unlimited fallback behavior."""
    with pytest.raises(ValueError, match="positive"):
        UsageWindow(seconds)


async def test_override_authority_lifecycle_removal_and_audit(test_container: AsyncContainer) -> None:
    """Private admin/CLI writes are audited, deletion filtered and retired rows retained."""
    user, _group = await usage_subjects(test_container)
    store = await test_container.get(UsagePolicyStore)
    policy = UsagePolicy(MEDIA_BUCKET, 8, 4, 2, 10)
    for actor, transport in ((ActorContext.telegram(USER), "private"), (ActorContext.system(), None)):
        with pytest.raises(PermissionError):
            await store.set_override(user.target, policy, actor=actor, transport_kind=transport)
    await provision_role(test_container, USER)
    with pytest.raises(PermissionError):
        await store.set_override(user.target, policy, actor=ActorContext.telegram(USER), transport_kind="group")
    assert await store.set_override(user.target, policy, actor=ActorContext.telegram(USER), transport_kind="private")
    assert not await store.set_override(user.target, policy, actor=ActorContext.cli())
    assert await store.set_override(user.target, policy, actor=ActorContext.cli(), remove=True)
    assert await store.list_overrides(user.target) == ()
    assert await store.set_override(user.target, policy, actor=ActorContext.cli())
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(UsageOverrideDB)) == 2
        audits = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "usage_override"))).all()
        )
        assert len(audits) == 3
        assert audits[0].changed_fields["limit"] == [None, 8]
        assert audits[1].changed_fields["limit"] == [8, None]
    await (await test_container.get(UserStore)).soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic")
    assert await store.list_overrides(user.target) == ()
    with pytest.raises(ValueError, match="active target"):
        await store.set_override(user.target, policy, actor=ActorContext.cli())


@pytest.mark.fresh_schema
async def test_audit_failure_rolls_back_policy_and_unknown_units_fail(test_container: AsyncContainer) -> None:
    """A real SQLite trigger rejects audit insertion after mutation; both rows must roll back."""
    user, _group = await usage_subjects(test_container)
    store = await test_container.get(UsagePolicyStore)
    policy = UsagePolicy(MEDIA_BUCKET, 8, 4, 2, 10)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        await reject_audit(session, "reject_usage_audit", "NEW.target_type = 'usage_override'", "synthetic")
        await session.commit()
    with pytest.raises(IntegrityError):
        await store.set_override(user.target, policy, actor=ActorContext.cli())
    assert await store.list_overrides(user.target) == ()
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(UsageOverrideDB)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.target_type == "usage_override")
            )
            == 0
        )
    with pytest.raises(ValueError, match="unsupported feature unit"):
        await store.set_override(user.target, replace(policy, unit=UsageUnit.TOKENS), actor=ActorContext.cli())
    with pytest.raises(ValueError, match="unsupported feature unit"):
        await store.set_override(user.target, replace(policy, bucket="family:unknown"), actor=ActorContext.cli())
    with pytest.raises(ValueError, match="trigger or unit"):
        await (await test_container.get(UsagePolicyService)).resolve(
            FEATURE, TriggerKind.DIRECT, USER, unit=UsageUnit.TOKENS
        )


async def test_resource_units_require_explicit_policies_without_provider_implementation(
    test_container: AsyncContainer,
) -> None:
    """Future units fail closed until both family and feature policy are configured."""
    user, _group = await usage_subjects(test_container)
    registry = FeatureRegistry(
        (
            FeatureDefinition(
                key="synthetic-resource",
                display_name="Synthetic resource",
                units=frozenset({UsageUnit.RESOURCE}),
                usage_policy="synthetic-resource",
            ),
        )
    )
    store = SQLAlchemyUsagePolicyStore(
        await test_container.get(async_sessionmaker),
        await test_container.get(AuditedUnitOfWork),
        await test_container.get(AuthorizationPolicy),
        registry,
    )
    service = UsagePolicyService(
        await test_container.get(UserStore),
        await test_container.get(ChatStore),
        registry,
        store,
        await test_container.get(PermissionService),
        UsageSafetyCaps(),
    )
    with pytest.raises(ValueError, match="policies are missing"):
        await service.resolve("synthetic-resource", TriggerKind.DIRECT, USER, unit=UsageUnit.RESOURCE)
    for bucket in ("family:synthetic-resource", "feature:synthetic-resource"):
        await store.set_override(
            user.target,
            UsagePolicy(bucket, 500, 100, 1, 10, UsageUnit.RESOURCE, UsageWindow(3600)),
            actor=ActorContext.cli(),
        )
    resolved = await service.resolve("synthetic-resource", TriggerKind.DIRECT, USER, unit=UsageUnit.RESOURCE)
    assert len(resolved.policies) == 2
    assert all(policy.unit == UsageUnit.RESOURCE and policy.window.seconds == 3600 for policy in resolved.policies)
