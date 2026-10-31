"""Public constructor contracts for runtime reservations, usage policies and safety bounds."""

import math
from dataclasses import FrozenInstanceError, replace

import pytest

from hovorunv2.application.usage.budgets import BudgetRequest, UnitAmount
from hovorunv2.application.usage.policies import (
    MEDIA_BUCKET,
    ResolvedUsage,
    UsagePolicy,
    UsageSafetyCaps,
    UsageSubject,
    UsageUnit,
)
from hovorunv2.domain.enums import TargetKind

POLICY = UsagePolicy(MEDIA_BUCKET, 6, 3, 1, 10)
SAFETY = UsageSafetyCaps()
USAGE = ResolvedUsage(UsageSubject(TargetKind.USER, 1, 1, 1), (POLICY,), False, SAFETY)
AMOUNT = UnitAmount(UsageUnit.REQUESTS, 1)
REQUEST = BudgetRequest("technical-id", USAGE, (AMOUNT,), 400.125)
MAX_EXACT_AMOUNT = (1 << 53) - 1
RESERVATION_ERROR = (
    "Reservation requires a technical ID, positive estimates for every policy unit and a finite deadline."
)
POLICY_LIMIT_ERROR = "Usage limits must be positive integers and burst cannot exceed the window limit."
POLICY_BUCKET_ERROR = "Usage bucket must identify a feature or family."
POLICY_DIMENSION_ERROR = "Usage policies require typed units and windows."
SAFETY_POSITIVE_ERROR = "Application safety caps must be positive integers."
SAFETY_BOUND_ERROR = "Per-request safety limits cannot exceed their application bounds."


@pytest.mark.parametrize(
    "overrides",
    [
        {"reservation_id": ""},
        {"reservation_id": "contains space"},
        {"reservation_id": "a" * 97},
        {"estimates": ()},
        {"estimates": (replace(AMOUNT, amount=0),)},
        {"estimates": (AMOUNT, AMOUNT)},
        {"estimates": (UnitAmount(UsageUnit.TOKENS, 1),)},
        {"deadline": math.nan},
        {"deadline": math.inf},
        {"deadline": -math.inf},
        {"deadline": 0},
        {"deadline": -1},
        {"reservation_id": "!", "deadline": None},
        {"estimates": (), "deadline": None},
        {"estimates": (AMOUNT, AMOUNT), "deadline": None},
    ],
)
def test_reservation_rejects_invalid_inputs_with_established_error(overrides: dict[str, object]) -> None:
    """Reject invalid requests and preserve earlier failures before later malformed values are inspected."""
    with pytest.raises(ValueError, match=RESERVATION_ERROR) as error:
        replace(REQUEST, **overrides)
    assert str(error.value) == RESERVATION_ERROR


def test_reservation_derives_units_before_validating_identity() -> None:
    """Preserve the original evaluation order for malformed estimate entries and an invalid identifier."""
    with pytest.raises(AttributeError, match="has no attribute 'unit'"):
        replace(REQUEST, estimates=(None,), reservation_id="!")


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit": "requests"},
        {"amount": True},
        {"amount": 1.0},
        {"amount": -1},
        {"amount": MAX_EXACT_AMOUNT + 1},
        {"unit": "requests", "amount": None},
    ],
)
def test_runtime_amount_rejects_untyped_or_inexact_values(overrides: dict[str, object]) -> None:
    """Keep exact integer bounds and unit validation ahead of malformed amount arithmetic."""
    with pytest.raises(ValueError, match="Runtime units require") as error:
        replace(AMOUNT, **overrides)
    assert str(error.value) == "Runtime units require a typed unit and a nonnegative exact integer."


@pytest.mark.parametrize("amount", [0, 1, MAX_EXACT_AMOUNT])
def test_runtime_amount_accepts_exact_boundaries(amount: int) -> None:
    """Final consumption may be zero and the largest exact runtime integer remains valid."""
    assert UnitAmount(UsageUnit.REQUESTS, amount).amount == amount


@pytest.mark.parametrize("field", ["limit", "burst", "concurrency", "pending"])
@pytest.mark.parametrize("value", [True, 1.0])
def test_policy_limits_require_exact_integers(field: str, value: object) -> None:
    """Booleans and integral floats must never become quota configuration."""
    with pytest.raises(ValueError, match=POLICY_LIMIT_ERROR) as error:
        replace(POLICY, **{field: value})
    assert str(error.value) == POLICY_LIMIT_ERROR


@pytest.mark.parametrize(
    ("overrides", "error_type", "message"),
    [
        ({"burst": POLICY.limit + 1}, ValueError, POLICY_LIMIT_ERROR),
        ({"bucket": "other:value"}, ValueError, POLICY_BUCKET_ERROR),
        ({"bucket": "feature:"}, ValueError, POLICY_BUCKET_ERROR),
        ({"bucket": ":feature"}, ValueError, POLICY_BUCKET_ERROR),
        ({"unit": "requests"}, TypeError, POLICY_DIMENSION_ERROR),
        ({"window": 60}, TypeError, POLICY_DIMENSION_ERROR),
        ({"limit": 0, "bucket": None, "unit": None}, ValueError, POLICY_LIMIT_ERROR),
        ({"bucket": "other:value", "unit": None}, ValueError, POLICY_BUCKET_ERROR),
    ],
)
def test_policy_validation_preserves_error_contract_and_precedence(
    overrides: dict[str, object], error_type: type[Exception], message: str
) -> None:
    """Numeric limits precede bucket validation, which precedes typed dimensions."""
    with pytest.raises(error_type, match=message) as error:
        replace(POLICY, **overrides)
    assert str(error.value) == message


@pytest.mark.parametrize(
    "field",
    [
        "global_concurrency",
        "global_pending",
        "payload_bytes",
        "total_payload_bytes",
        "deadline_seconds",
        "execution_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, 1.0])
def test_safety_caps_require_positive_exact_integers(field: str, value: object) -> None:
    """Every safety ceiling must remain a real positive integer."""
    with pytest.raises(ValueError, match=SAFETY_POSITIVE_ERROR) as error:
        replace(SAFETY, **{field: value})
    assert str(error.value) == SAFETY_POSITIVE_ERROR


@pytest.mark.parametrize(
    "overrides",
    [
        {"payload_bytes": SAFETY.total_payload_bytes + 1},
        {"execution_seconds": SAFETY.deadline_seconds + 1},
    ],
)
def test_request_safety_cannot_exceed_global_bounds(overrides: dict[str, object]) -> None:
    """Payload and execution bounds remain independent mandatory invariants."""
    with pytest.raises(ValueError, match=SAFETY_BOUND_ERROR) as error:
        replace(SAFETY, **overrides)
    assert str(error.value) == SAFETY_BOUND_ERROR


def test_positive_caps_are_validated_before_cross_field_bounds() -> None:
    """A malformed ceiling must fail before comparing other request/application bounds."""
    with pytest.raises(ValueError, match=SAFETY_POSITIVE_ERROR) as error:
        replace(SAFETY, global_pending=0, payload_bytes=SAFETY.total_payload_bytes + 1)
    assert str(error.value) == SAFETY_POSITIVE_ERROR


def test_valid_reservation_retains_fractional_deadline_and_frozen_value_semantics() -> None:
    """Constructor decomposition must not alter DTO fields, hashing or immutability."""
    assert replace(REQUEST) == REQUEST
    assert hash(replace(REQUEST)) == hash(REQUEST)
    assert REQUEST.deadline == 400.125
    with pytest.raises(FrozenInstanceError):
        REQUEST.deadline = 500
