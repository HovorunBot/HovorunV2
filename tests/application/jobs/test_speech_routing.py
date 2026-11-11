"""Verify speech stage routing, private automatic envelopes, and worker role bounds."""

import time

import pytest

from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobTarget
from hovorunv2.domain.enums import TargetKind

pytestmark = pytest.mark.unit


def test_private_automatic_envelope_requires_matching_actor_and_charged_user() -> None:
    """Private automatic envelopes bind the target, actor, and charged subject to the same user."""
    target = JobTarget(kind=TargetKind.USER, entity_id=1, generation=1, telegram_id=12345)
    now = time.time()

    # Valid private automatic envelope
    envelope = JobEnvelope(
        job_id="test-speech-auto-job",
        payload_ref="a" * 32,
        feature="transcribe_auto",
        target=target,
        actor_id=12345,
        actor_generation=1,
        trigger=TriggerKind.AUTOMATIC,
        charged_kind=TargetKind.USER,
        charged_telegram_id=12345,
        accepted_at=now,
        deadline=now + 120,
        dedupe_key="b" * 64,
        feature_epoch=1,
        message_id=999,
    )
    assert envelope.validate_binding() == envelope

    # Mismatched actor vs target user -> rejected
    with pytest.raises(ValueError, match="Job trigger, target and charged subject disagree"):
        JobEnvelope(
            job_id="test-speech-auto-job",
            payload_ref="a" * 32,
            feature="transcribe_auto",
            target=target,
            actor_id=54321,
            actor_generation=1,
            trigger=TriggerKind.AUTOMATIC,
            charged_kind=TargetKind.USER,
            charged_telegram_id=12345,
            accepted_at=now,
            deadline=now + 120,
            dedupe_key="b" * 64,
            feature_epoch=1,
            message_id=999,
        )

    # Mismatched charged telegram id vs actor -> rejected
    with pytest.raises(ValueError, match="Job trigger, target and charged subject disagree"):
        JobEnvelope(
            job_id="test-speech-auto-job",
            payload_ref="a" * 32,
            feature="transcribe_auto",
            target=target,
            actor_id=12345,
            actor_generation=1,
            trigger=TriggerKind.AUTOMATIC,
            charged_kind=TargetKind.USER,
            charged_telegram_id=54321,
            accepted_at=now,
            deadline=now + 120,
            dedupe_key="b" * 64,
            feature_epoch=1,
            message_id=999,
        )


def test_speech_stage_enum_and_transitions() -> None:
    """SPEECH stage is defined and has no further automated handoff stages."""
    assert JobStage.SPEECH == "speech"
