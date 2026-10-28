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
    return role is not None and policy.is_allowed(PermissionFacts(role, action, PermissionTarget("bot")))


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
    admin = (
        request.target.kind == "group"
        and request.group_admin is not None
        and request.group_admin.applies_to(request.actor_id, request.target, now)
    )
    allowed = policy.is_allowed(PermissionFacts(role, request.action, request.target, admin))
    return AccessDecision(allowed, DecisionReason.ALLOWED if allowed else DecisionReason.UNAUTHORIZED_MANAGEMENT)
