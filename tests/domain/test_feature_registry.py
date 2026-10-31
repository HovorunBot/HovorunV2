"""Stable code-defined feature metadata and media routing contracts."""

import pytest

from hovorunv2.application.features.registry import (
    COMMAND_PRESENTATION,
    FEATURE_REGISTRY,
    FeatureChannel,
    FeatureDefinition,
    FeatureRegistry,
    FeatureScope,
    TriggerKind,
)
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, CommandName
from hovorunv2.infrastructure.fixtures import INITIAL_COMMANDS


def test_media_registry_preserves_all_persisted_keys_and_resolves_triggers() -> None:
    """Every provider mode retains its existing key for private and group actions."""
    expected = {str(key) for splits in PROVIDER_SPLIT_COMMANDS.values() for key in splits}
    assert {feature.key for feature in FEATURE_REGISTRY.all()} == expected
    fixtures = {entry["name"] for entry in INITIAL_COMMANDS}
    assert expected <= fixtures
    for provider, (video, post) in PROVIDER_SPLIT_COMMANDS.items():
        for key, is_video in ((video, True), (post, False)):
            feature = FEATURE_REGISTRY.capture(provider, is_video=is_video)
            assert feature.key == key
            assert feature.provider == provider
            assert feature.is_video == is_video
            assert feature.channel == FeatureChannel.STANDARD
            assert feature.scopes == {FeatureScope.PRIVATE, FeatureScope.GROUP}
            assert feature.triggers == {TriggerKind.DIRECT, TriggerKind.AUTOMATIC}
            assert feature.units == {"requests"}
            assert feature.required_bot_capabilities == frozenset()


def test_registry_rejects_unknown_duplicate_and_implicit_beta_activation() -> None:
    """Invalid definitions fail before execution; beta defaults never activate."""
    definition = FeatureDefinition("test_beta", "Test beta", channel=FeatureChannel.BETA)
    registry = FeatureRegistry((definition,))
    assert registry.get("missing") is None
    with pytest.raises(ValueError, match="Unknown feature key"):
        registry.require("missing")
    with pytest.raises(ValueError, match="Duplicate feature key"):
        FeatureRegistry((definition, definition))
    with pytest.raises(ValueError, match="Beta features must start disabled"):
        FeatureDefinition("test_beta", "Test beta", channel=FeatureChannel.BETA, default_enabled=True)
    with pytest.raises(ValueError, match="Unknown media provider"):
        registry.capture(CommandName.HELP, is_video=True)


def test_command_presentation_contains_no_access_exemption() -> None:
    """Discoverability and protected settings are separate from execution authority."""
    assert COMMAND_PRESENTATION[CommandName.SETTINGS].discoverable
    assert not COMMAND_PRESENTATION[CommandName.SETTINGS].toggleable
    assert FEATURE_REGISTRY.get(CommandName.SETTINGS) is None
