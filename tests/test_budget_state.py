"""Pure persisted-budget binding, settlement and fingerprint contracts."""

import json
from dataclasses import replace

import pytest

from hovorunv2.application.budgets import RUNTIME_RETENTION_SECONDS, BudgetRequest, ReservationState, UnitAmount
from hovorunv2.application.usage import ResolvedUsage, UsagePolicy, UsageSafetyCaps, UsageSubject, UsageUnit
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.budget_state import (
    MAX_EXACT_INTEGER,
    BudgetStateUnavailableError,
    build_record,
    parse_record,
)

PREFIX = "{budget-state}:budget:"
USAGE = ResolvedUsage(
    UsageSubject(TargetKind.USER, 101, 1, 1),
    (UsagePolicy("feature:test", 6, 3, 1, 10), UsagePolicy("family:media", 6, 3, 1, 10)),
    False,
    UsageSafetyCaps(),
)
REQUEST = BudgetRequest("budget-state", USAGE, (UnitAmount(UsageUnit.REQUESTS, 1),), 400)


def test_window_and_baseline_changes_preserve_request_fingerprint() -> None:
    """Windows and durable seeds may move without changing the original reservation operation."""
    first = build_record(PREFIX, REQUEST, 120, (0, 0))
    later = build_record(PREFIX, REQUEST, 180, (2, 3))
    assert first.fingerprint == later.fingerprint
    assert first.quotas[0].key != later.quotas[0].key
    assert later.quotas[0].baseline == 2
    assert parse_record(first.serialized(), PREFIX, REQUEST.reservation_id) == first
    assert parse_record(later.serialized(), PREFIX, REQUEST.reservation_id) == later
    changed = build_record(PREFIX, replace(REQUEST, deadline=401), 120, (0, 0))
    assert changed.fingerprint != first.fingerprint


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "wrong-reservation"},
        {"concurrency": []},
        {"estimates": {}},
        {"retention": RUNTIME_RETENTION_SECONDS + 1},
        {"state": ReservationState.WAITING},
        {"estimates": {"requests": 0}},
        {"estimates": {"requests": MAX_EXACT_INTEGER + 1}},
        {"estimates": {"unknown": 1}},
        {"state": ReservationState.COMMITTED, "actual": None},
        {"state": ReservationState.COMMITTED, "actual": {"requests": 0}},
        {"state": ReservationState.RELEASED, "actual": {"requests": 1}},
        {"state": ReservationState.COMMITTED, "actual": {"tokens": 1}},
        {"state": ReservationState.COMMITTED, "actual": {"requests": 2}},
        {"state": ReservationState.RELEASED, "actual": {"requests": -1}},
    ],
)
def test_invalid_required_binding_and_settlement_are_unavailable(overrides: dict[str, object]) -> None:
    """Malformed required facts cannot authorize a reservation replay or settlement."""
    record = build_record(PREFIX, REQUEST, 120, (0, 0)).model_dump(mode="json")
    record.update(overrides)
    with pytest.raises(BudgetStateUnavailableError):
        parse_record(json.dumps(record), PREFIX, REQUEST.reservation_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"key": PREFIX + "quota:user:101:feature:test:requests:60:121"},
        {"rate": PREFIX + "rate:unrelated"},
        {"ends": 181},
        {"amount": 2},
        {"unit": "tokens"},
        {"seconds": 61},
        {"burst": 7},
    ],
)
def test_quota_window_and_resource_binding_is_required(overrides: dict[str, object]) -> None:
    """A well-shaped record still needs canonical quota, rate and window ownership."""
    record = build_record(PREFIX, REQUEST, 120, (0, 0)).model_dump(mode="json")
    record["quotas"][0].update(overrides)
    with pytest.raises(BudgetStateUnavailableError):
        parse_record(json.dumps(record), PREFIX, REQUEST.reservation_id)


@pytest.mark.parametrize("kind", [TargetKind.USER, TargetKind.GROUP])
def test_legacy_empty_quotas_are_only_accepted_for_personal_bypass(kind: TargetKind) -> None:
    """Old empty-object quotas remain readable for personal bypass without permitting group bypass."""
    usage = replace(USAGE, bypass_personal_quota=True)
    record = build_record(PREFIX, replace(REQUEST, usage=usage), 120, (0, 0)).model_dump(mode="json")
    record["quotas"] = {}
    if kind == TargetKind.GROUP:
        record["subject"] = "group:-101"
        with pytest.raises(BudgetStateUnavailableError):
            parse_record(json.dumps(record), PREFIX, REQUEST.reservation_id)
        return
    parsed = parse_record(json.dumps(record), PREFIX, REQUEST.reservation_id)
    assert not parsed.quotas
    assert len(parsed.concurrency) == 1


@pytest.mark.parametrize("state", [ReservationState.COMMITTED, ReservationState.RELEASED])
def test_terminal_record_requires_matching_actual_consumption(state: ReservationState) -> None:
    """A valid terminal receipt retains exact original resource keys and estimates."""
    original = build_record(PREFIX, REQUEST, 120, (0, 0))
    actual = {"requests": 1 if state == ReservationState.COMMITTED else 0}
    settled = original.model_copy(update={"state": state, "actual": actual})
    assert parse_record(settled.serialized(), PREFIX, REQUEST.reservation_id) == settled
