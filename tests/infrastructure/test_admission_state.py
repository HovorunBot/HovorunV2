"""Pure admission corruption precedence, capacity binding and transition-fencing regressions."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from hovorunv2.application.admission import AdmissionUnavailableError, JobStage, TicketState
from hovorunv2.application.feature_jobs import EnqueueState, JobOutcome
from hovorunv2.infrastructure.admission_state import (
    AdmissionCapacity,
    AdmissionOperation,
    AdmissionRecord,
    AdmissionTransition,
    admission_keys,
    calculate_change,
    envelope_json,
    read_capacity,
)
from tests.utils.jobs import job_envelope
from tests.utils.runtime import ManualClock

OTHER_OWNER = "other"
JOB = job_envelope("a" * 32, ManualClock())
KEYS = admission_keys("test:{pure}:", JOB)
RECORD = AdmissionRecord(
    envelope=envelope_json(JOB),
    state=TicketState.ACCEPTED,
    stage=JobStage.MEDIA,
    byte_size=16,
    attempt=0,
    token="",
    published=0,
    not_before=0,
    subject_key=KEYS.subject,
    ingress_key=KEYS.ingress,
    payload_key=KEYS.payload,
    dedupe_key=KEYS.dedupe,
)
CAPACITY = AdmissionCapacity(1, RECORD.byte_size, 1, 1)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"not_before": JOB.deadline, "state": TicketState.RUNNING},
            "Admission schedule exceeds its original deadline.",
        ),
        ({"state": TicketState.RUNNING}, "Active admission requires an execution owner and attempt."),
        (
            {"state": TicketState.CANCELLING, "token": "owner", "outcome": JobOutcome.COMPLETE},
            "Active admission requires an execution owner and attempt.",
        ),
        (
            {"token": "owner", "outcome": JobOutcome.COMPLETE},
            "Queued admission cannot retain an execution owner.",
        ),
        ({"state": TicketState.TERMINAL}, "Admission terminal state requires exactly one safe outcome."),
        ({"outcome": JobOutcome.COMPLETE}, "Admission terminal state requires exactly one safe outcome."),
    ],
)
def test_record_lifecycle_preserves_validation_precedence(updates: dict[str, object], message: str) -> None:
    """Schedule and execution ownership fail before terminal outcome consistency."""
    with pytest.raises(ValidationError) as error:
        AdmissionRecord.model_validate(RECORD.model_dump() | updates)
    assert error.value.errors()[0]["msg"] == f"Value error, {message}"


@pytest.mark.parametrize("field", ["subject_key", "ingress_key", "payload_key", "dedupe_key"])
def test_record_pointers_cannot_redirect_to_another_ticket(field: str) -> None:
    """Every persisted pointer is bound to the immutable original envelope."""
    corrupt = RECORD.model_copy(update={field: "other:ticket"})
    with pytest.raises(AdmissionUnavailableError, match="pointers disagree"):
        corrupt.keys("test:{pure}:")


@pytest.mark.parametrize(
    "capacity",
    [
        replace(CAPACITY, count=0),
        replace(CAPACITY, byte_size=15),
        replace(CAPACITY, subject=0),
        replace(CAPACITY, ingress=0),
    ],
)
def test_active_ticket_requires_each_capacity_dimension(capacity: AdmissionCapacity) -> None:
    """A missing count or byte dimension must not authorize release or execution."""
    with pytest.raises(AdmissionUnavailableError, match="Active admission capacity"):
        capacity.require_active(RECORD, JOB.job_id, JOB.deadline, JOB.deadline)


@pytest.mark.parametrize(
    ("pointer", "score", "handoff_score"),
    [("other-job", JOB.deadline, JOB.deadline), (JOB.job_id, 0, JOB.deadline), (JOB.job_id, JOB.deadline, None)],
)
def test_active_indexes_bind_identity_and_original_deadline(
    pointer: str, score: float, handoff_score: float | None
) -> None:
    """Accepted capacity requires both indexes and the exact original dedupe target."""
    with pytest.raises(AdmissionUnavailableError, match="Active admission capacity"):
        CAPACITY.require_active(RECORD, pointer, score, handoff_score)


def test_invalid_pointer_precedes_capacity_and_unqueued_handoff_is_optional() -> None:
    """Decode errors retain precedence; only accepted tickets require a handoff score."""
    with pytest.raises(AdmissionUnavailableError, match="string metadata"):
        replace(CAPACITY, count=0).require_active(RECORD, b"\xff", JOB.deadline, JOB.deadline)
    CAPACITY.require_active(RECORD.model_copy(update={"state": TicketState.ENQUEUED}), JOB.job_id, JOB.deadline, None)


@pytest.mark.parametrize(
    ("totals", "subject", "ingress", "pending", "message"),
    [
        ({"count": 1}, 1, 1, 1, "Admission totals are unavailable."),
        ({"count": "invalid", "bytes": 16, "other": 1}, 1, 1, 1, "Admission totals are unavailable."),
        ({"count": True, "bytes": 16}, 1, 1, 1, "Admission capacity metadata is unavailable."),
        ({"count": 1, "bytes": 16}, 1, 1, 0, "Admission totals disagree with pending capacity."),
        ({"count": 0, "bytes": 16}, 0, 0, 0, "Admission totals disagree with pending capacity."),
        ({"count": 1, "bytes": 16}, 2, 1, 1, "Admission totals disagree with pending capacity."),
    ],
)
def test_capacity_shape_and_consistency_errors_remain_distinct(
    totals: dict[str, object], subject: int, ingress: int, pending: int, message: str
) -> None:
    """Malformed counters and inconsistent active ledgers never become empty capacity."""
    with pytest.raises(AdmissionUnavailableError, match=message) as error:
        read_capacity(totals, subject, ingress, pending)
    assert str(error.value) == message


def test_fresh_namespace_and_byte_keyed_active_snapshot_are_valid() -> None:
    """An unused namespace is empty; a complete stored snapshot retains exact counters."""
    assert read_capacity({}, None, None, 0) == AdmissionCapacity(0, 0, 0, 0)
    assert read_capacity({b"count": b"1", b"bytes": b"16"}, b"1", b"1", 1) == CAPACITY


@pytest.mark.parametrize("state", [TicketState.RUNNING, TicketState.CANCELLING, TicketState.TERMINAL])
def test_wrong_completion_owner_cannot_release_or_rewrite_capacity(state: TicketState) -> None:
    """Owner fences apply to running cancellation and terminal replay alike."""
    record = RECORD.model_copy(
        update={
            "state": state,
            "token": "owner",
            "attempt": 1,
            "outcome": JobOutcome.COMPLETE if state == TicketState.TERMINAL else None,
        }
    )
    result = calculate_change(
        record, AdmissionTransition(AdmissionOperation.FINISH, token=OTHER_OWNER, outcome=JobOutcome.COMPLETE)
    )
    assert result.record is None
    assert not result.write
    assert not result.release


def test_terminal_replay_is_idempotent_and_conflicting_outcome_raises() -> None:
    """A matching terminal replay succeeds without another release; a new outcome conflicts."""
    record = RECORD.model_copy(update={"state": TicketState.TERMINAL, "outcome": JobOutcome.COMPLETE})
    replay = calculate_change(record, AdmissionTransition(AdmissionOperation.FINISH, outcome=JobOutcome.COMPLETE))
    assert replay.record == record
    assert replay.success
    assert not replay.write
    assert not replay.release
    with pytest.raises(ValueError, match="terminal state conflicts"):
        calculate_change(record, AdmissionTransition(AdmissionOperation.FINISH, outcome=JobOutcome.FAILED))


@pytest.mark.parametrize("state", [TicketState.ACCEPTED, TicketState.ENQUEUED, TicketState.RUNNING])
def test_deadline_expiry_cannot_steal_a_running_owner(state: TicketState) -> None:
    """Late claims release queued work only; running ownership survives the same clock boundary."""
    record = RECORD.model_copy(
        update={"state": state, "token": "owner" if state == TicketState.RUNNING else "", "attempt": 1}
    )
    result = calculate_change(
        record, AdmissionTransition(AdmissionOperation.CLAIM, now=JOB.deadline, token=OTHER_OWNER)
    )
    if state == TicketState.RUNNING:
        assert result.record is None
        assert not result.release
        return
    assert result.record is not None
    assert result.record.outcome == JobOutcome.EXPIRED
    assert result.release
    assert result.write


@pytest.mark.parametrize(("token", "stage"), [("other", JobStage.BROWSER), ("owner", JobStage.MEDIA)])
def test_handoff_requires_the_current_owner_and_forward_stage(token: str, stage: JobStage) -> None:
    """Incorrect owner or stage cannot transfer a running ticket's capacity."""
    record = RECORD.model_copy(update={"state": TicketState.RUNNING, "token": "owner", "attempt": 1})
    with pytest.raises(ValueError, match="handoff token conflicts"):
        calculate_change(record, AdmissionTransition(AdmissionOperation.HANDOFF, stage=stage, token=token))


def test_publication_replay_keeps_schedule_and_stale_acknowledgement_is_a_noop() -> None:
    """Repeated registration retains its first schedule and attempt fencing prevents stale acknowledgement."""
    record = RECORD.model_copy(update={"publication_registered": 1, "not_before": 180})
    replay = calculate_change(
        record, AdmissionTransition(AdmissionOperation.REGISTER, expected_envelope=JOB, not_before=200)
    )
    assert replay.record == record
    assert replay.should_publish
    assert not replay.write
    assert replay.publication_state == EnqueueState.EXISTS
    stale = calculate_change(record, AdmissionTransition(AdmissionOperation.PUBLISHED, attempt=1))
    assert stale.record == record
    assert not stale.write
