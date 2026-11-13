"""Aggregate usage views for own and group scopes against real PostgreSQL and Valkey."""

import time

from dishka import AsyncContainer

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.speech.policies import SPEECH_BUCKET
from hovorunv2.application.usage.policies import (
    MEDIA_BUCKET,
    UsagePolicy,
    UsagePolicyStore,
    UsageSafetyCaps,
    UsageUnit,
    UsageWindow,
)
from hovorunv2.application.usage.usage_ledger import (
    AccountingSubject,
    UsageCharge,
    UsageEvent,
    UsageLedgerStore,
)
from hovorunv2.application.usage.usage_view import FeatureUsage, UsageSummary, UsageViewService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from tests.conftest import provision_role
from tests.utils.usage import GROUP, USER, usage_subjects

WINDOW_SECONDS = 60
FEATURE = MEDIA_BUCKET
ADMIN = 360020


def _observed() -> int:
    """Align controlled accounting time to one deterministic window."""
    return (int(time.time()) // WINDOW_SECONDS) * WINDOW_SECONDS


async def _charge(container: AsyncContainer, subject: AccountingSubject, amount: int = 1) -> int:
    """Apply one idempotent aggregate charge for the selected stable subject."""
    ledger = await container.get(UsageLedgerStore)
    observed = _observed()
    event = UsageEvent(
        f"usage-view-{subject.kind.value}-{subject.telegram_id}",
        subject,
        observed,
        (UsageCharge(MEDIA_BUCKET, amount),),
    )
    await ledger.apply_batch((event,), actor=ActorContext.system())
    return observed


def _feature_entry(summary: UsageSummary, key: str) -> FeatureUsage:
    """Select the requested feature line from a summary."""
    return next(entry for entry in summary.features if entry.feature == key)


async def test_user_summary_reports_consumed_and_remaining(test_container: AsyncContainer) -> None:
    """Own scope reads the actor's stable accounting totals and effective default limit."""
    await usage_subjects(test_container)
    await _charge(test_container, AccountingSubject(TargetKind.USER, USER), amount=2)
    views = await test_container.get(UsageViewService)
    summary = await views.for_user(USER)
    assert summary.subject_kind == TargetKind.USER
    assert summary.subject_telegram_id == USER
    assert summary.unlimited_personal_quota is False
    assert {entry.feature for entry in summary.features} == {
        MEDIA_BUCKET,
        SPEECH_BUCKET,
        "feature:transcribe",
        "feature:translate",
    }
    feature = _feature_entry(summary, FEATURE)
    assert "shared across all providers" in feature.display_name
    assert feature.unit == UsageUnit.REQUESTS
    assert feature.window_seconds == WINDOW_SECONDS
    assert feature.consumed == 2
    assert feature.limit == 6
    assert feature.remaining == 4
    assert summary.safety == UsageSafetyCaps()


async def test_admin_summary_separates_unlimited_quota_from_safety_caps(
    test_container: AsyncContainer,
) -> None:
    """Administrator personal quota is unlimited while safety ceilings remain explicit."""
    await usage_subjects(test_container)
    await provision_role(test_container, ADMIN)
    views = await test_container.get(UsageViewService)
    summary = await views.for_user(ADMIN)
    assert summary.unlimited_personal_quota is True
    assert summary.safety == UsageSafetyCaps()
    assert summary.safety.global_concurrency == 2
    assert summary.safety.global_pending == 500


async def test_group_scope_never_includes_member_personal_usage(test_container: AsyncContainer) -> None:
    """Group-charged totals are independent of another member's direct personal totals."""
    await usage_subjects(test_container)
    views = await test_container.get(UsageViewService)
    observed = await _charge(test_container, AccountingSubject(TargetKind.GROUP, GROUP), amount=2)
    await _charge(test_container, AccountingSubject(TargetKind.USER, USER), amount=5)
    group_summary = await views.for_group(GROUP)
    personal_summary = await views.for_user(USER)
    assert {entry.feature for entry in group_summary.features} == {
        MEDIA_BUCKET,
        SPEECH_BUCKET,
        "feature:transcribe_auto",
    }
    assert _feature_entry(group_summary, FEATURE).limit == 12
    assert group_summary.subject_kind == TargetKind.GROUP
    assert personal_summary.subject_kind == TargetKind.USER
    assert _feature_entry(group_summary, FEATURE).consumed == 2
    assert _feature_entry(personal_summary, FEATURE).consumed == 5
    ledger = await test_container.get(UsageLedgerStore)
    assert (
        await ledger.total(
            AccountingSubject(TargetKind.GROUP, GROUP),
            MEDIA_BUCKET,
            UsageUnit.REQUESTS,
            UsageWindow(WINDOW_SECONDS),
            observed,
        )
        == 2
    )


async def test_usage_survives_entity_generation_change(test_container: AsyncContainer) -> None:
    """Stable-subject aggregates persist when the internal entity generation changes."""
    await usage_subjects(test_container)
    observed = await _charge(test_container, AccountingSubject(TargetKind.USER, USER), amount=3)
    ledger = await test_container.get(UsageLedgerStore)
    subject = AccountingSubject(TargetKind.USER, USER)
    user_store = await test_container.get(UserStore)
    await user_store.soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic generation change")
    assert await ledger.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(WINDOW_SECONDS), observed) == 3


async def test_shared_quota_keeps_distinct_windows(test_container: AsyncContainer) -> None:
    """Deduplicate shared buckets without merging independent time-window limits."""
    await usage_subjects(test_container)
    user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
    assert user is not None
    store = await test_container.get(UsagePolicyStore)
    hourly_seconds = 3600
    hourly_limit = 30
    await store.set_override(
        PermissionTarget(TargetKind.USER.value, user.id, user.generation, USER),
        UsagePolicy(MEDIA_BUCKET, hourly_limit, 3, 1, 10, window=UsageWindow(hourly_seconds)),
        actor=ActorContext.cli(),
    )
    summary = await (await test_container.get(UsageViewService)).for_user(USER)
    assert {(entry.feature, entry.window_seconds, entry.limit) for entry in summary.features} == {
        (MEDIA_BUCKET, WINDOW_SECONDS, 6),
        (MEDIA_BUCKET, hourly_seconds, hourly_limit),
        (SPEECH_BUCKET, WINDOW_SECONDS, 6),
        ("feature:transcribe", WINDOW_SECONDS, 6),
        ("feature:translate", WINDOW_SECONDS, 6),
    }
