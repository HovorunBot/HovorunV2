"""Typed reservation records and pure budget calculations shared by both Valkey clients."""

import hashlib
import json
import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hovorunv2.application.budgets import (
    RUNTIME_RETENTION_SECONDS,
    TECHNICAL_ID,
    BudgetRequest,
    ReservationDecision,
    ReservationState,
    UnitAmount,
)
from hovorunv2.application.usage import UsageUnit
from hovorunv2.domain.enums import TargetKind

MAX_EXACT_INTEGER = (1 << 53) - 1
TRANSACTION_ATTEMPTS = 32


class BudgetStateUnavailableError(ValueError):
    """Stored resource ownership cannot be verified safely."""


class QuotaSpec(BaseModel):
    """One immutable quota window and its shared token bucket."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)

    key: str
    rate: str
    unit: str
    amount: int = Field(gt=0, le=MAX_EXACT_INTEGER)
    baseline: int = Field(ge=0, le=MAX_EXACT_INTEGER)
    limit: int = Field(gt=0, le=MAX_EXACT_INTEGER)
    burst: int = Field(gt=0, le=MAX_EXACT_INTEGER)
    seconds: int = Field(gt=0, le=MAX_EXACT_INTEGER)
    ends: int = Field(gt=0)


class ConcurrencySpec(BaseModel):
    """One concurrency set whose members retain ownership until verified settlement."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    key: str
    limit: int = Field(gt=0, le=MAX_EXACT_INTEGER)


class BudgetRecord(BaseModel):
    """Compatible operational reservation metadata without private payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)

    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,96}$")
    subject: str = Field(pattern=r"^(user|group):-?[1-9][0-9]*$")
    generation: int = Field(gt=0)
    deadline: float = Field(gt=0)
    estimates: dict[str, int]
    quotas: tuple[QuotaSpec, ...]
    concurrency: tuple[ConcurrencySpec, ...]
    now: float = Field(ge=0)
    retention: int = Field(gt=0)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: ReservationState = ReservationState.RESERVED
    actual: dict[str, int] | None = None

    def resource_keys(self) -> tuple[str, ...]:
        """Return every dependent key that must be watched before reading resource snapshots."""
        return tuple(
            dict.fromkeys(
                (
                    *[key for quota in self.quotas for key in (quota.key, quota.rate)],
                    *[item.key for item in self.concurrency],
                )
            )
        )

    def serialized(self) -> str:
        """Encode explicit JSON values while preserving exact Python float timestamps."""
        return json.dumps(self.model_dump(mode="json"), separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class QuotaUpdate:
    """Validated arithmetic ready for one atomic quota and rate mutation."""

    quota: QuotaSpec
    used: int
    held: int
    tokens: float
    at: float


@dataclass(frozen=True, slots=True)
class BudgetMutation:
    """All planned resource updates and their public decision."""

    decision: ReservationDecision
    updates: tuple[QuotaUpdate, ...] = ()
    record: BudgetRecord | None = None


def build_record(prefix: str, request: BudgetRequest, now: float, totals: tuple[int, ...]) -> BudgetRecord:
    """Keep the original request fingerprint independent of current window keys and durable totals."""
    usage = request.usage
    subject = f"{usage.subject.kind.value}:{usage.subject.telegram_id}"
    estimates = {amount.unit.value: amount.amount for amount in request.estimates}
    quotas, concurrency = _build_resource_specs(prefix, request, now, totals, subject, estimates)
    binding = {
        "subject": subject,
        "generation": usage.subject.generation,
        "deadline": request.deadline,
        "estimates": estimates,
        "quotas": [quota.model_dump(mode="json") for quota in quotas],
        "concurrency": [item.model_dump(mode="json") for item in concurrency],
    }
    identity = {
        **binding,
        "quotas": [
            {
                key: value
                for key, value in quota.model_dump(mode="json").items()
                if key not in {"baseline", "key", "ends"}
            }
            for quota in quotas
        ],
    }
    return BudgetRecord(
        subject=subject,
        generation=usage.subject.generation,
        deadline=request.deadline,
        estimates=estimates,
        quotas=tuple(quotas),
        concurrency=tuple(concurrency),
        id=request.reservation_id,
        now=now,
        retention=RUNTIME_RETENTION_SECONDS,
        fingerprint=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
    )


def _build_resource_specs(
    prefix: str, request: BudgetRequest, now: float, totals: tuple[int, ...], subject: str, estimates: dict[str, int]
) -> tuple[tuple[QuotaSpec, ...], tuple[ConcurrencySpec, ...]]:
    usage = request.usage
    quotas = []
    concurrency = [ConcurrencySpec(key=prefix + "global", limit=usage.safety.global_concurrency)]
    bypass = usage.bypass_personal_quota and usage.subject.kind == TargetKind.USER
    for policy, baseline in zip(usage.policies, totals, strict=True):
        scope = f"{subject}:{policy.bucket}:{policy.unit.value}:{policy.window.seconds}"
        start = policy.window.start(int(now))
        if bypass:
            continue
        quotas.append(
            QuotaSpec(
                key=prefix + f"quota:{scope}:{start}",
                rate=prefix + f"rate:{scope}",
                unit=policy.unit.value,
                amount=estimates[policy.unit.value],
                baseline=baseline,
                limit=policy.limit,
                burst=policy.burst,
                seconds=policy.window.seconds,
                ends=start + policy.window.seconds,
            )
        )
        concurrency.append(ConcurrencySpec(key=prefix + f"concurrency:{scope}", limit=policy.concurrency))
    return tuple(quotas), tuple(concurrency)


def validate_reserve_args(request: BudgetRequest, now: float, durable_totals: tuple[int, ...]) -> None:
    """Reject incomplete totals and preserve the existing exact counter range."""
    if (
        not math.isfinite(now)
        or now < 0
        or len(durable_totals) != len(request.usage.policies)
        or any(type(total) is not int or not 0 <= total <= MAX_EXACT_INTEGER for total in durable_totals)
    ):
        msg = "Reservations require finite time and exact nonnegative durable totals for every policy."
        raise ValueError(msg)
    values = [request.usage.safety.global_concurrency]
    for policy in request.usage.policies:
        values.extend((policy.limit, policy.burst, policy.concurrency, policy.window.seconds))
    if any(value > MAX_EXACT_INTEGER for value in values):
        msg = "Runtime policy exceeds exact counter capacity."
        raise ValueError(msg)
    scopes = {(policy.bucket, policy.unit, policy.window.seconds) for policy in request.usage.policies}
    if len(scopes) != len(request.usage.policies):
        msg = "Reservations require distinct policy dimensions."
        raise ValueError(msg)


def validate_finish_args(reservation_id: str, actual: tuple[UnitAmount, ...], now: float) -> dict[str, int]:
    """Validate technical identity, finite time and unique settlement dimensions."""
    if TECHNICAL_ID.fullmatch(reservation_id) is None or not math.isfinite(now) or now < 0 or not actual:
        msg = "Settlement requires a technical reservation ID, finite time and actual amounts."
        raise ValueError(msg)
    amounts = {item.unit.value: item.amount for item in actual}
    if len(amounts) != len(actual):
        msg = "Settlement units must be unique."
        raise ValueError(msg)
    return amounts


def parse_record(raw: object, prefix: str, reservation_id: str) -> BudgetRecord:
    """Reject malformed or misbound records using safe technical errors only."""
    try:
        if not isinstance(raw, str | bytes):
            raise BudgetStateUnavailableError
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("quotas") == {}:
            data["quotas"] = []
        record = BudgetRecord.model_validate(data)
        _require_record_binding(record, reservation_id)
        _require_resource_namespace(record, prefix)
        _require_estimate_coverage(record)
        _verify_record_keys(record, prefix)
        _require_settlement(record)
    except ValidationError, ValueError, TypeError:
        raise BudgetStateUnavailableError from None
    return record


def _require_record_binding(record: BudgetRecord, reservation_id: str) -> None:
    if record.id != reservation_id:
        raise BudgetStateUnavailableError
    if not record.concurrency or not record.estimates:
        raise BudgetStateUnavailableError
    if record.retention != RUNTIME_RETENTION_SECONDS:
        raise BudgetStateUnavailableError
    if not record.quotas and not record.subject.startswith("user:"):
        raise BudgetStateUnavailableError
    if record.state not in {ReservationState.RESERVED, ReservationState.COMMITTED, ReservationState.RELEASED}:
        raise BudgetStateUnavailableError


def _require_resource_namespace(record: BudgetRecord, prefix: str) -> None:
    if any(not key.startswith(prefix) for key in record.resource_keys()):
        raise BudgetStateUnavailableError


def _require_estimate_coverage(record: BudgetRecord) -> None:
    if any(type(value) is not int or not 0 < value <= MAX_EXACT_INTEGER for value in record.estimates.values()):
        raise BudgetStateUnavailableError
    if any(unit not in {item.value for item in UsageUnit} for unit in record.estimates):
        raise BudgetStateUnavailableError
    if any(
        quota.unit not in record.estimates or quota.amount != record.estimates[quota.unit] for quota in record.quotas
    ):
        raise BudgetStateUnavailableError


def _require_settlement(record: BudgetRecord) -> None:
    if record.state == ReservationState.RESERVED:
        return
    actual = record.actual
    if actual is None:
        raise BudgetStateUnavailableError
    if actual.keys() != record.estimates.keys():
        raise BudgetStateUnavailableError
    if any(not 0 <= amount <= record.estimates[unit] for unit, amount in actual.items()):
        raise BudgetStateUnavailableError
    if (record.state == ReservationState.COMMITTED) != any(actual.values()):
        raise BudgetStateUnavailableError


def _verify_record_keys(record: BudgetRecord, prefix: str) -> None:
    expected = [prefix + "global"]
    for quota in record.quotas:
        scope = _require_quota_key(quota, record, prefix)
        expected.append(prefix + "concurrency:" + scope)
    if [item.key for item in record.concurrency] != expected or len(set(expected)) != len(expected):
        raise BudgetStateUnavailableError


def _require_quota_key(quota: QuotaSpec, record: BudgetRecord, prefix: str) -> str:
    if not quota.key.startswith(prefix + "quota:" + record.subject + ":"):
        raise BudgetStateUnavailableError
    scope, _, window = quota.key.removeprefix(prefix + "quota:").rpartition(":")
    bucket, _, dimensions = scope.removeprefix(record.subject + ":").rpartition(":")
    bucket, _, unit = bucket.rpartition(":")
    start = int(window)
    if not bucket.startswith(("feature:", "family:")) or not bucket.partition(":")[2]:
        raise BudgetStateUnavailableError
    if unit != quota.unit or dimensions != str(quota.seconds):
        raise BudgetStateUnavailableError
    _require_quota_window(quota, record.now, start)
    if quota.rate != prefix + "rate:" + scope:
        raise BudgetStateUnavailableError
    if quota.burst > quota.limit:
        raise BudgetStateUnavailableError
    return scope


def _require_quota_window(quota: QuotaSpec, now: float, start: int) -> None:
    if start < 0 or start % quota.seconds:
        raise BudgetStateUnavailableError
    if start != int(now) - int(now) % quota.seconds:
        raise BudgetStateUnavailableError
    if quota.ends != start + quota.seconds:
        raise BudgetStateUnavailableError


def _text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            raise BudgetStateUnavailableError from None
    if isinstance(value, str):
        return value
    raise BudgetStateUnavailableError


def parse_hash(raw: object) -> dict[str, str]:
    """Decode a Redis hash while rejecting unexpected protocol shapes."""
    if not isinstance(raw, dict):
        raise BudgetStateUnavailableError
    return {_text(key): _text(value) for key, value in raw.items()}


def parse_members(raw: object) -> frozenset[str]:
    """Decode set membership used to verify reservation ownership."""
    if not isinstance(raw, set | frozenset):
        raise BudgetStateUnavailableError
    return frozenset(_text(value) for value in raw)


def _integer(values: dict[str, str], key: str) -> int:
    try:
        value = int(values[key])
        if not 0 <= value <= MAX_EXACT_INTEGER:
            raise BudgetStateUnavailableError
    except KeyError, ValueError:
        raise BudgetStateUnavailableError from None
    return value


def _number(values: dict[str, str], key: str) -> float:
    try:
        value = float(values[key])
        if not math.isfinite(value) or value < 0:
            raise BudgetStateUnavailableError
    except KeyError, ValueError:
        raise BudgetStateUnavailableError from None
    return value


def calculate_reserve(
    record: BudgetRecord,
    hashes: dict[str, dict[str, str]],
    members: dict[str, frozenset[str]],
) -> BudgetMutation:
    """Compute all-or-nothing window, burst and concurrency reservations from watched snapshots."""
    retry = record.now
    updates = []
    for quota in record.quotas:
        if quota.amount > min(quota.burst, quota.limit):
            return BudgetMutation(ReservationDecision(ReservationState.IMPOSSIBLE))
        window, rate = hashes[quota.key], hashes[quota.rate]
        if (window and not rate) or (not window and any(members[item.key] for item in record.concurrency[1:])):
            raise BudgetStateUnavailableError
        used = max(_integer(window, "used") if window else 0, quota.baseline)
        held = _integer(window, "held") if window else 0
        tokens = _number(rate, "tokens") if rate else float(quota.burst)
        at = _number(rate, "at") if rate else record.now
        tokens = min(quota.burst, tokens + max(0, record.now - at) * quota.limit / quota.seconds)
        if not math.isfinite(tokens) or held + quota.amount > MAX_EXACT_INTEGER:
            raise BudgetStateUnavailableError
        if used + held + quota.amount > quota.limit:
            retry = max(retry, quota.ends)
        if tokens < quota.amount:
            retry = max(retry, record.now + (quota.amount - tokens) * quota.seconds / quota.limit)
        updates.append(QuotaUpdate(quota, used, held + quota.amount, tokens - quota.amount, record.now))
    for item in record.concurrency:
        if len(members[item.key]) >= item.limit:
            retry = max(retry, record.now + 1)
    if retry > record.now:
        return BudgetMutation(ReservationDecision(ReservationState.WAITING, retry))
    return BudgetMutation(ReservationDecision(ReservationState.RESERVED), tuple(updates), record)


def verify_active_ownership(
    record: BudgetRecord,
    hashes: dict[str, dict[str, str]],
    members: dict[str, frozenset[str]],
) -> None:
    """Missing resources never authorize a replay, capacity refund or partial settlement."""
    for quota in record.quotas:
        window, rate = hashes[quota.key], hashes[quota.rate]
        if _integer(window, "held") < quota.amount:
            raise BudgetStateUnavailableError
        _integer(window, "used")
        if _number(rate, "tokens") > quota.burst:
            raise BudgetStateUnavailableError
        _number(rate, "at")
    if any(record.id not in members[item.key] for item in record.concurrency):
        raise BudgetStateUnavailableError


def calculate_finish(
    record: BudgetRecord,
    actual: dict[str, int],
    now: float,
    hashes: dict[str, dict[str, str]],
    members: dict[str, frozenset[str]],
) -> BudgetMutation:
    """Plan a verified terminal settlement without mutating resource snapshots."""
    if actual.keys() != record.estimates.keys() or any(
        amount > record.estimates[unit] for unit, amount in actual.items()
    ):
        msg = "Reservation ID or settlement conflicts with its original operation."
        raise ValueError(msg)
    if record.state != ReservationState.RESERVED:
        if record.actual != actual:
            msg = "Reservation ID or settlement conflicts with its original operation."
            raise ValueError(msg)
        return BudgetMutation(ReservationDecision(record.state))
    verify_active_ownership(record, hashes, members)
    updates = []
    for quota in record.quotas:
        window, rate = hashes[quota.key], hashes[quota.rate]
        tokens = min(
            quota.burst,
            _number(rate, "tokens")
            + max(0, now - _number(rate, "at")) * quota.limit / quota.seconds
            + quota.amount
            - actual[quota.unit],
        )
        if not math.isfinite(tokens) or _integer(window, "used") + actual[quota.unit] > MAX_EXACT_INTEGER:
            raise BudgetStateUnavailableError
        updates.append(
            QuotaUpdate(
                quota,
                _integer(window, "used") + actual[quota.unit],
                _integer(window, "held") - quota.amount,
                tokens,
                now,
            )
        )
    state = ReservationState.COMMITTED if any(actual.values()) else ReservationState.RELEASED
    settled = record.model_copy(update={"actual": actual, "state": state})
    return BudgetMutation(ReservationDecision(state), tuple(updates), settled)
