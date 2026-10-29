"""Pure current-authority decisions, independent of asynchronous fact collection."""

from datetime import datetime

from hovorunv2.application.permissions import (
    AccessDecision,
    DecisionReason,
    PermissionFacts,
    PermissionRequest,
    PermissionTarget,
)
from hovorunv2.application.ports import AuthorizationPolicy
from hovorunv2.domain.enums import Role


def authority_allowed(policy: AuthorizationPolicy, role: Role | None, action: str) -> bool:
    """Historical assignments and trust never substitute for the current active role."""
    if role is None:
        return False
    return policy.is_allowed(PermissionFacts(role, action, PermissionTarget("bot")))


def _has_current_group_administrator(request: PermissionRequest, now: datetime) -> bool:
    """Use optional administrator evidence only for its exact group, actor and current interval."""
    if request.target.kind != "group":
        return False
    evidence = request.group_admin
    if evidence is None:
        return False
    return evidence.applies_to(request.actor_id, request.target, now)


def permission_decision(
    request: PermissionRequest,
    role: Role | None,
    policy: AuthorizationPolicy,
    now: datetime,
    *,
    target_active: bool,
    group_banned: bool,
) -> AccessDecision:
    """Apply identity, transport, group restrictions and exact administrator evidence."""
    if role is None or not target_active:
        return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)
    if request.target.kind == "bot" and request.transport_kind != "private":
        return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)
    if request.target.kind == "group" and group_banned:
        return AccessDecision(allowed=False, reason=DecisionReason.GROUP_BANNED)
    admin = _has_current_group_administrator(request, now)
    allowed = policy.is_allowed(PermissionFacts(role, request.action, request.target, admin))
    return AccessDecision(allowed, DecisionReason.ALLOWED if allowed else DecisionReason.UNAUTHORIZED_MANAGEMENT)
