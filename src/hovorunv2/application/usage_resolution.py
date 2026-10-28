"""Pure bounded usage resolution shared by bot and worker fact loaders."""

from hovorunv2.application.features import FeatureDefinition
from hovorunv2.application.usage import (
    MINUTE_SECONDS,
    ResolvedUsage,
    UsagePolicy,
    UsageSafetyCaps,
    UsageSubject,
    UsageUnit,
)
from hovorunv2.domain.enums import TargetKind

NORMAL_REQUEST_LIMIT = 6
NORMAL_BURST = 3
NORMAL_CONCURRENCY = 1
NORMAL_PENDING = 10
TRUSTED_REQUEST_LIMIT = 12
TRUSTED_BURST = 6
TRUSTED_CONCURRENCY = 2
GROUP_PENDING = 25


def resolve_usage(
    definition: FeatureDefinition,
    subject: UsageSubject,
    overrides: tuple[UsagePolicy, ...],
    safety: UsageSafetyCaps,
    *,
    trusted: bool,
    bypass: bool,
    unit: UsageUnit,
) -> ResolvedUsage:
    """Intersect feature/family restrictions; unsupported units never become unlimited."""
    buckets = {f"feature:{definition.key}", f"family:{definition.usage_policy}"}
    policies: dict[tuple[str, int], UsagePolicy] = {}
    group = subject.kind == TargetKind.GROUP
    elevated = group or trusted
    if unit == UsageUnit.REQUESTS and definition.usage_policy == "media":
        for bucket in sorted(buckets):
            policies[bucket, MINUTE_SECONDS] = UsagePolicy(
                bucket,
                TRUSTED_REQUEST_LIMIT if elevated else NORMAL_REQUEST_LIMIT,
                TRUSTED_BURST if elevated else NORMAL_BURST,
                TRUSTED_CONCURRENCY if elevated else NORMAL_CONCURRENCY,
                GROUP_PENDING if group else NORMAL_PENDING,
            )
    for override in overrides:
        if override.bucket in buckets and override.unit == unit:
            policies[override.bucket, override.window.seconds] = override
    if any(not any(policy.bucket == bucket for policy in policies.values()) for bucket in buckets):
        msg = "Usage policies are missing; unsupported resources cannot run with unlimited defaults."
        raise ValueError(msg)
    return ResolvedUsage(subject, tuple(policies.values()), bypass and not group, safety)
