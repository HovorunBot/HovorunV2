"""Verify speech mode invariants, qualified limits and request-unit policies."""

from dataclasses import replace

import pytest

from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.speech.policies import SpeechLimits, speech_selected
from hovorunv2.application.usage.policies import UsagePolicy, UsageSafetyCaps, UsageSubject, UsageUnit
from hovorunv2.application.usage.usage_resolution import resolve_usage
from hovorunv2.domain.enums import CommandName, SpeechMode, TargetKind

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("mode", "manual", "automatic"),
    [
        (SpeechMode.OFF, False, False),
        (SpeechMode.MANUAL_ONLY, True, False),
        (SpeechMode.MANUAL_AND_AUTOMATIC, True, True),
    ],
)
def test_three_state_mode_cannot_select_automatic_without_manual(
    mode: SpeechMode, manual: bool, automatic: bool
) -> None:
    """Derive both trigger decisions from one supported state."""
    assert speech_selected(mode, CommandName.TRANSCRIBE) is manual
    assert speech_selected(mode, CommandName.TRANSCRIBE_AUTO) is automatic
    with pytest.raises(ValueError, match="not a valid SpeechMode"):
        SpeechMode("automatic_only")


@pytest.mark.parametrize("field", ["audio_seconds", "audio_bytes", "transcript_chars", "compute_threads"])
@pytest.mark.parametrize("invalid", [0, -1, True])
def test_speech_limits_reject_unbounded_or_boolean_values(field: str, invalid: int) -> None:
    """Reject accidental unlimited resource settings at their ownership boundary."""
    with pytest.raises(ValueError, match="qualified bounds"):
        replace(SpeechLimits(), **{field: invalid})


@pytest.mark.parametrize(
    ("field", "invalid"),
    [("audio_seconds", 121), ("audio_bytes", 10_000_001), ("transcript_chars", 32_001), ("compute_threads", 3)],
)
def test_operators_cannot_raise_limits_beyond_the_qualified_envelope(field: str, invalid: int) -> None:
    """Require fresh qualification before accepting larger inputs or compute budgets."""
    with pytest.raises(ValueError, match="qualified bounds"):
        replace(SpeechLimits(), **{field: invalid})


@pytest.mark.parametrize("invalid", [0, -1, float("inf"), float("nan"), True])
def test_idle_interval_must_be_finite_and_positive(invalid: float) -> None:
    """Keep inactive models from remaining resident through invalid timer settings."""
    with pytest.raises(ValueError, match="idle timeout"):
        SpeechLimits(model_idle_seconds=invalid)


def test_speech_features_start_off_with_exact_scopes_and_triggers() -> None:
    """Register metadata without introducing default activation or media provider routing."""
    for command, trigger in (
        (CommandName.TRANSCRIBE, TriggerKind.DIRECT),
        (CommandName.TRANSCRIBE_AUTO, TriggerKind.AUTOMATIC),
    ):
        feature = FEATURE_REGISTRY.require(command)
        assert not feature.default_enabled
        assert feature.triggers == {trigger}
        assert feature.units == {UsageUnit.REQUESTS}
        assert feature.usage_policy == feature.queue_policy == "speech"
        assert feature.provider is None


@pytest.mark.parametrize("kind", [TargetKind.USER, TargetKind.GROUP])
@pytest.mark.parametrize("trusted", [False, True])
def test_request_quota_overrides_preserve_speech_defaults_and_global_safety(kind: TargetKind, trusted: bool) -> None:
    """Use existing request units with bounded speech defaults and unchanged application ceilings."""
    feature = FEATURE_REGISTRY.require(CommandName.TRANSCRIBE)
    subject = UsageSubject(kind, 630001 if kind == TargetKind.USER else -630001, 1, 1)
    safety = UsageSafetyCaps()
    override = UsagePolicy("family:speech", 2, 1, 1, 2)
    result = resolve_usage(feature, subject, (override,), safety, trusted=trusted, bypass=True, unit=UsageUnit.REQUESTS)
    assert result.safety == safety
    assert len(result.policies) == 2
    assert override in result.policies
    assert all(policy.concurrency == 1 for policy in result.policies)
    assert all(policy.unit == UsageUnit.REQUESTS for policy in result.policies)
    assert result.bypass_personal_quota is (kind == TargetKind.USER)
