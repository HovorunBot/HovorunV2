"""Authoritative feature emergency state, epochs, and gating rules against real PostgreSQL."""

import json
from dataclasses import replace

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.features.emergency import FeatureEmergencyState, envelope_is_current
from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.features.feature_policy import FeatureFacts, evaluate_feature
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureScope, TriggerKind
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.emergency import FeatureEmergencyDB
from hovorunv2.domain.enums import CommandName
from tests.conftest import provision_role

PROVIDER = CommandName.TWITTER
SPLIT = FEATURE_REGISTRY.capture(PROVIDER, is_video=True)
ADMIN = 490001
PLAIN = 490002


def _facts(**overrides: object) -> FeatureFacts:
    """Build a fully enabled private-feature fact snapshot."""
    base = FeatureFacts(
        FeatureScope.PRIVATE,
        target_active=True,
        actor_active=True,
        private_access=True,
        eligible=True,
        selected=True,
        capable=True,
    )
    return replace(base, **overrides)


def test_disabled_emergency_denies_execution_and_discovery() -> None:
    """A disabled feature is neither executable nor discoverable for any caller."""
    allowed = evaluate_feature(SPLIT, _facts(), trigger=TriggerKind.DIRECT)
    assert allowed.allowed
    assert allowed.visible
    denied = evaluate_feature(SPLIT, _facts(emergency_disabled=True), trigger=TriggerKind.DIRECT)
    assert not denied.allowed
    assert not denied.visible


def test_stale_epoch_is_rejected_after_disable_and_re_enable() -> None:
    """Work accepted before a transition never becomes valid again after re-enable."""
    enabled = FeatureEmergencyState("twitter", disabled=False, epoch=1)
    assert envelope_is_current(1, enabled)
    assert not envelope_is_current(1, FeatureEmergencyState("twitter", disabled=True, epoch=2))
    assert not envelope_is_current(1, FeatureEmergencyState("twitter", disabled=False, epoch=3))
    assert envelope_is_current(3, FeatureEmergencyState("twitter", disabled=False, epoch=3))


@pytest.mark.parametrize(("origin", "transport"), [("ordinary", "private"), ("telegram", "group"), ("system", None)])
async def test_emergency_transitions_require_private_current_authority(
    test_container: AsyncContainer, origin: str, transport: str | None
) -> None:
    """Only CLI or a current private bot administrator may change emergency state."""
    await provision_role(test_container, ADMIN)
    await (await test_container.get(UserStore)).ingest_user(PLAIN)
    service = await test_container.get(FeatureEmergencyService)
    actor = (
        ActorContext.system() if origin == "system" else ActorContext.telegram(ADMIN if origin == "telegram" else PLAIN)
    )
    with pytest.raises(PermissionError):
        await service.set_state(
            PROVIDER.value, disabled=True, reason="synthetic", actor=actor, transport_kind=transport
        )
    with pytest.raises(ValueError, match="internal reason"):
        await service.set_state(PROVIDER.value, disabled=True, reason="   ", actor=ActorContext.cli())


async def test_disable_and_enable_audit_atomically_advance_epoch(test_container: AsyncContainer) -> None:
    """Transitions persist one authoritative row and its audit event; split keys normalize."""
    await provision_role(test_container, ADMIN)
    service = await test_container.get(FeatureEmergencyService)
    baseline = await service.state(PROVIDER.value)
    assert not baseline.disabled
    assert baseline.epoch == 0
    disabled = await service.set_state(
        PROVIDER.value,
        disabled=True,
        reason="synthetic outage",
        actor=ActorContext.telegram(ADMIN),
        transport_kind="private",
    )
    assert disabled.disabled
    assert disabled.epoch == 1
    assert disabled.reason == "synthetic outage"
    enabled = await service.set_state(
        PROVIDER.value, disabled=False, reason="synthetic recovery", actor=ActorContext.cli()
    )
    assert not enabled.disabled
    assert enabled.epoch == 2
    assert (await service.state(SPLIT.key)).epoch == enabled.epoch
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        row = await session.scalar(select(FeatureEmergencyDB))
        assert row is not None
        assert not row.disabled
        assert row.epoch == 2
        audits = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "feature_emergency"))).all()
        )
        assert [audit.action for audit in audits] == ["disable", "enable"]
        assert all("synthetic" not in json.dumps(audit.changed_fields) for audit in audits)


async def test_public_reason_is_separate_from_internal_notes(test_container: AsyncContainer) -> None:
    """Public-safe outage text is stored apart from internal notes and never audited verbatim."""
    await provision_role(test_container, ADMIN)
    service = await test_container.get(FeatureEmergencyService)
    state = await service.set_state(
        PROVIDER.value,
        disabled=True,
        reason="internal incident note",
        public_reason="Temporary provider outage",
        actor=ActorContext.cli(),
    )
    assert state.reason == "internal incident note"
    assert state.public_reason == "Temporary provider outage"
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        row = await session.scalar(select(FeatureEmergencyDB))
        assert row is not None
        assert row.reason == "internal incident note"
        assert row.public_reason == "Temporary provider outage"
        audit = await session.scalar(select(AuditEventDB).where(AuditEventDB.target_type == "feature_emergency"))
        assert audit is not None
        assert audit.changed_fields["public_reason_changed"] is True
        assert "Temporary provider outage" not in json.dumps(audit.changed_fields)
    enabled = await service.set_state(
        PROVIDER.value, disabled=False, reason="synthetic recovery", actor=ActorContext.cli()
    )
    assert enabled.public_reason is None
