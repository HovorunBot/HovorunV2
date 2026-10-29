"""Quota ordering, accounting validation precedence and stable replay identity contracts."""

from dataclasses import replace

import pytest

from hovorunv2.application.features import FeatureDefinition, FeatureRegistry
from hovorunv2.application.usage import MEDIA_BUCKET, UsagePolicy, UsageSafetyCaps, UsageSubject, UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import (
    MAX_ACCOUNTING_INTEGER,
    MAX_BUCKET_LENGTH,
    MAX_USAGE_BATCH,
    AccountingSubject,
    UsageCharge,
    UsageEvent,
    validate_usage_batch,
)
from hovorunv2.application.usage_resolution import resolve_usage
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin, TargetKind

FEATURE = FeatureDefinition("usage-feature", "Usage feature", units=frozenset(unit.value for unit in UsageUnit))
FEATURE_BUCKET = f"feature:{FEATURE.key}"
SUBJECT = UsageSubject(TargetKind.USER, 101, 1, 1)
SAFETY = UsageSafetyCaps()
CHARGE = UsageCharge(FEATURE_BUCKET, 2)
EVENT = UsageEvent("ledger-event", AccountingSubject(TargetKind.USER, 101), 120, (CHARGE, UsageCharge(MEDIA_BUCKET, 3)))
REGISTRY = FeatureRegistry((replace(FEATURE, units=frozenset({UsageUnit.REQUESTS.value})),))
EVENT_SHAPE_ERROR = "Accounting requires a bounded technical event ID and nonempty bounded charges."
TIMESTAMP_ERROR = "Accounting timestamp must be a nonnegative signed 64-bit integer."


@pytest.mark.parametrize(
    ("kind", "trusted", "limits"),
    [
        (TargetKind.USER, False, (6, 3, 1, 10)),
        (TargetKind.USER, True, (12, 6, 2, 10)),
        (TargetKind.GROUP, False, (12, 6, 2, 25)),
        (TargetKind.GROUP, True, (12, 6, 2, 25)),
    ],
)
def test_media_defaults_and_personal_bypass_remain_subject_scoped(
    kind: TargetKind, trusted: bool, limits: tuple[int, int, int, int]
) -> None:
    """Group ceilings remain independent of trust and never gain personal quota bypass."""
    subject = replace(SUBJECT, kind=kind, telegram_id=101 if kind == TargetKind.USER else -101)
    result = resolve_usage(FEATURE, subject, (), SAFETY, trusted=trusted, bypass=True, unit=UsageUnit.REQUESTS)
    assert {policy.bucket for policy in result.policies} == {FEATURE_BUCKET, MEDIA_BUCKET}
    assert all((policy.limit, policy.burst, policy.concurrency, policy.pending) == limits for policy in result.policies)
    assert result.bypass_personal_quota is (kind == TargetKind.USER)
    assert result.safety is SAFETY


def test_override_replacement_preserves_policy_order_and_independent_windows() -> None:
    """The last matching dimension replaces in place; other windows remain additional restrictions."""
    first = UsagePolicy(MEDIA_BUCKET, 20, 4, 2, 7)
    longer = replace(first, window=UsageWindow(3600))
    last = replace(first, limit=30)
    overrides = (first, longer, replace(first, unit=UsageUnit.TOKENS), last, replace(first, bucket="family:other"))
    result = resolve_usage(FEATURE, SUBJECT, overrides, SAFETY, trusted=False, bypass=False, unit=UsageUnit.REQUESTS)
    assert len(result.policies) == 3
    assert result.policies[0] == last
    assert result.policies[1].bucket == FEATURE_BUCKET
    assert result.policies[1].limit == 6
    assert result.policies[2] == longer


@pytest.mark.parametrize("unit", [UsageUnit.RESOURCE, UsageUnit.TOKENS])
@pytest.mark.parametrize("buckets", [(), (FEATURE_BUCKET,), (MEDIA_BUCKET,)])
def test_resource_units_require_both_feature_and_family_policies(unit: UsageUnit, buckets: tuple[str, ...]) -> None:
    """Partial explicit overrides must not become an unlimited resource policy."""
    overrides = tuple(UsagePolicy(bucket, 10, 5, 1, 10, unit) for bucket in buckets)
    with pytest.raises(ValueError, match="Usage policies are missing"):
        resolve_usage(FEATURE, SUBJECT, overrides, SAFETY, trusted=True, bypass=True, unit=unit)


@pytest.mark.parametrize("unit", [UsageUnit.RESOURCE, UsageUnit.TOKENS])
def test_complete_resource_overrides_keep_their_original_order(unit: UsageUnit) -> None:
    """Without media request defaults, explicit policy tuple order is the insertion order."""
    overrides = tuple(UsagePolicy(bucket, 10, 5, 1, 10, unit) for bucket in (FEATURE_BUCKET, MEDIA_BUCKET))
    result = resolve_usage(FEATURE, SUBJECT, overrides, SAFETY, trusted=False, bypass=False, unit=unit)
    assert result.policies == overrides


@pytest.mark.parametrize(
    ("overrides", "error_type", "message"),
    [
        ({"amount": True}, ValueError, "Accounting amount must be a positive signed 64-bit integer."),
        ({"amount": 1.0}, ValueError, "Accounting amount must be a positive signed 64-bit integer."),
        (
            {"amount": MAX_ACCOUNTING_INTEGER + 1},
            ValueError,
            "Accounting amount must be a positive signed 64-bit integer.",
        ),
        ({"bucket": "family:"}, ValueError, "Accounting bucket must identify a bounded feature or family."),
        ({"bucket": "other:value"}, ValueError, "Accounting bucket must identify a bounded feature or family."),
        (
            {"bucket": "family:" + "x" * MAX_BUCKET_LENGTH},
            ValueError,
            "Accounting bucket must identify a bounded feature or family.",
        ),
        ({"unit": "requests"}, TypeError, "Accounting requires typed units and windows."),
        ({"window": 60}, TypeError, "Accounting requires typed units and windows."),
        (
            {"window": UsageWindow(MAX_ACCOUNTING_INTEGER + 1)},
            ValueError,
            "Accounting window exceeds database integer capacity.",
        ),
        ({"amount": 0, "bucket": None}, ValueError, "Accounting amount must be a positive signed 64-bit integer."),
        (
            {"bucket": "family:", "unit": None},
            ValueError,
            "Accounting bucket must identify a bounded feature or family.",
        ),
        ({"unit": None, "window": None}, TypeError, "Accounting requires typed units and windows."),
    ],
)
def test_charge_validation_preserves_errors_and_order(
    overrides: dict[str, object], error_type: type[Exception], message: str
) -> None:
    """Amount precedes bucket validation, then typed dimensions precede database window capacity."""
    with pytest.raises(error_type, match=message) as error:
        replace(CHARGE, **overrides)
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"event_id": "!", "occurred_at": None}, EVENT_SHAPE_ERROR),
        ({"charges": (), "occurred_at": None}, EVENT_SHAPE_ERROR),
        ({"charges": (CHARGE,) * (MAX_USAGE_BATCH + 1), "occurred_at": None}, EVENT_SHAPE_ERROR),
        ({"occurred_at": True}, TIMESTAMP_ERROR),
        ({"occurred_at": -1}, TIMESTAMP_ERROR),
        ({"occurred_at": MAX_ACCOUNTING_INTEGER + 1}, TIMESTAMP_ERROR),
        ({"occurred_at": None, "charges": (None,)}, TIMESTAMP_ERROR),
    ],
)
def test_event_shape_precedes_timestamp_and_dimension_access(overrides: dict[str, object], message: str) -> None:
    """Malformed event containers fail before later fields and charge dimensions are accessed."""
    with pytest.raises(ValueError, match=message) as error:
        replace(EVENT, **overrides)
    assert str(error.value) == message


def test_fingerprint_is_stable_across_constructor_refactoring_and_charge_order() -> None:
    """Existing durable receipts must keep their exact canonical digest."""
    assert EVENT.fingerprint == "f68dea55a5cc6963f344a4a40cebdd1abee0834fece539742e6957aeab529ee4"
    assert replace(EVENT, charges=tuple(reversed(EVENT.charges))).fingerprint == EVENT.fingerprint


@pytest.mark.parametrize("origin", [ActorOrigin.SYSTEM, ActorOrigin.CLI])
def test_trusted_origin_accepts_empty_usage_batch(origin: ActorOrigin) -> None:
    """A legitimate no-op remains valid for the same trusted accounting origins."""
    validate_usage_batch(REGISTRY, (), ActorContext(origin=origin))


@pytest.mark.parametrize("origin", [ActorOrigin.TELEGRAM, ActorOrigin.MIGRATION])
def test_origin_validation_precedes_batch_bounds_and_registry_lookup(origin: ActorOrigin) -> None:
    """An invalid origin cannot reach registry validation even in an oversized unsupported batch."""
    unknown = replace(EVENT, charges=(replace(CHARGE, bucket="feature:unknown"),))
    with pytest.raises(PermissionError, match="trusted system or CLI origin"):
        validate_usage_batch(REGISTRY, (unknown,) * (MAX_USAGE_BATCH + 1), ActorContext(origin=origin))


@pytest.mark.parametrize(
    ("charge", "message"),
    [
        (replace(CHARGE, bucket="feature:unknown"), "Unknown feature key: unknown"),
        (replace(CHARGE, bucket="family:unknown"), "Accounting bucket or unit is unsupported by the feature registry."),
        (replace(CHARGE, unit=UsageUnit.TOKENS), "Accounting bucket or unit is unsupported by the feature registry."),
    ],
)
def test_batch_registry_validation_preserves_required_lookup_errors(charge: UsageCharge, message: str) -> None:
    """Missing features retain their semantic lookup error; missing family/unit support remains rejected."""
    with pytest.raises(ValueError, match=message) as error:
        validate_usage_batch(REGISTRY, (replace(EVENT, charges=(charge,)),), ActorContext.system())
    assert str(error.value) == message
