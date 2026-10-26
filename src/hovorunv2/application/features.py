"""Typed feature definitions independent of command presentation and access policy."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from hovorunv2.domain.enums import DEFAULT_COMMANDS, PROVIDER_SPLIT_COMMANDS, CommandName


class FeatureChannel(StrEnum):
    """Release channel used by eligibility rules."""

    STANDARD = "standard"
    BETA = "beta"


class FeatureScope(StrEnum):
    """Supported feature configuration and execution targets."""

    PRIVATE = "private"
    GROUP = "group"


class TriggerKind(StrEnum):
    """How feature work enters the application."""

    DIRECT = "direct"
    AUTOMATIC = "automatic"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """Code-owned stable identity, capabilities and policy references for a feature."""

    key: str
    display_name: str
    channel: FeatureChannel = FeatureChannel.STANDARD
    introduction: str = "legacy-media"
    default_enabled: bool = False
    scopes: frozenset[FeatureScope] = frozenset({FeatureScope.PRIVATE, FeatureScope.GROUP})
    triggers: frozenset[TriggerKind] = frozenset({TriggerKind.DIRECT, TriggerKind.AUTOMATIC})
    required_bot_capabilities: frozenset[str] = frozenset()
    units: frozenset[str] = frozenset({"requests"})
    usage_policy: str = "media"
    queue_policy: str = "media"
    notification_category: str = "feature_status"
    provider: CommandName | None = None
    is_video: bool | None = None

    def __post_init__(self) -> None:
        """Reject incomplete definitions and implicit beta activation."""
        if not self.key or not self.display_name or not self.introduction or not self.scopes or not self.triggers:
            msg = "Feature identity, introduction, scopes and triggers must be defined"
            raise ValueError(msg)
        if not self.units or not self.usage_policy or not self.queue_policy or not self.notification_category:
            msg = "Feature usage, queue and notification policies must be defined"
            raise ValueError(msg)
        if self.channel == FeatureChannel.BETA and self.default_enabled:
            msg = "Beta features must start disabled"
            raise ValueError(msg)


class FeatureRegistry:
    """Validate unique stable keys and resolve definitions without durable grant mirrors."""

    def __init__(self, definitions: Iterable[FeatureDefinition]) -> None:
        self._definitions: dict[str, FeatureDefinition] = {}
        for definition in definitions:
            if definition.key in self._definitions:
                msg = f"Duplicate feature key: {definition.key}"
                raise ValueError(msg)
            self._definitions[definition.key] = definition

    def get(self, key: str) -> FeatureDefinition | None:
        """Return a known definition without inventing a permissive fallback."""
        return self._definitions.get(key)

    def require(self, key: str) -> FeatureDefinition:
        """Resolve a protected feature or reject an unknown key."""
        definition = self.get(key)
        if definition is None:
            msg = f"Unknown feature key: {key}"
            raise ValueError(msg)
        return definition

    def all(self) -> tuple[FeatureDefinition, ...]:
        """Return a stable snapshot of code-defined metadata."""
        return tuple(self._definitions.values())

    def capture(self, provider: CommandName, *, is_video: bool) -> FeatureDefinition:
        """Resolve provider capture to its canonical persisted split feature."""
        splits = PROVIDER_SPLIT_COMMANDS.get(provider)
        if splits is None:
            msg = f"Unknown media provider: {provider}"
            raise ValueError(msg)
        return self.require(str(splits[0] if is_video else splits[1]))


FEATURE_REGISTRY = FeatureRegistry(
    FeatureDefinition(
        key=str(key),
        display_name=f"{provider.capitalize()} {'video' if is_video else 'image/text'} links",
        default_enabled=True,
        provider=provider,
        is_video=is_video,
    )
    for provider, splits in PROVIDER_SPLIT_COMMANDS.items()
    for key, is_video in zip(splits, (True, False), strict=True)
)


@dataclass(frozen=True, slots=True)
class CommandPresentation:
    """Discoverability and configuration metadata; never an authorization exemption."""

    discoverable: bool = True
    toggleable: bool = False


COMMAND_PRESENTATION: dict[str, CommandPresentation] = {command: CommandPresentation() for command in DEFAULT_COMMANDS}
