"""Whole-group beta selection and current administrator support boundaries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hovorunv2.application.features import FeatureChannel, FeatureDefinition, FeatureScope
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.domain.audit import ActorContext


@dataclass(frozen=True, slots=True)
class GroupBetaFacts:
    """Feature-bound selection and current support; independent of group approval."""

    feature: str
    subject_active: bool
    selected: bool = False
    group_granted: bool = False
    qualifying_enabler: bool = False

    def permits(self, feature: FeatureDefinition, *, group_allowed: bool, emergency_disabled: bool) -> bool:
        """Require explicit independent access/emergency guards for group execution."""
        return (
            self.subject_active
            and self.feature == feature.key
            and feature.channel == FeatureChannel.BETA
            and FeatureScope.GROUP in feature.scopes
            and self.selected
            and (self.group_granted or self.qualifying_enabler)
            and group_allowed
            and not emergency_disabled
        )


class GroupBetaStore(Protocol):
    """Read live beta support and mutate selected state with atomic authority checks."""

    async def facts(self, target: PermissionTarget, feature: str, now: datetime) -> GroupBetaFacts:
        """Resolve active selection, group grant and current recorded eligible administrators."""
        ...

    async def set_enabled(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        enabled: bool,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        """Recheck authority and eligibility inside the audited transaction; report actual changes."""
        ...
