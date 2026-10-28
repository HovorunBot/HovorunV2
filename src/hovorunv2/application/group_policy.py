"""Pure group approval and administrator blocker precedence."""

from dataclasses import dataclass

from hovorunv2.application.permissions import AccessDecision, DecisionReason


@dataclass(frozen=True, slots=True)
class GroupAccessFacts:
    """Current operational facts, never a reusable access grant."""

    active: bool = False
    banned: bool = False
    admins_fresh: bool = False
    banned_admin: bool = False
    unknown_admin: bool = False
    approved: bool = False


def group_access(facts: GroupAccessFacts) -> AccessDecision:
    """Preserve absolute bans, fresh administrator evidence and durable approval order."""
    reason = DecisionReason.ALLOWED
    if not facts.active:
        reason = DecisionReason.ENTITY_UNAVAILABLE
    elif facts.banned:
        reason = DecisionReason.GROUP_BANNED
    elif not facts.admins_fresh or (facts.unknown_admin and not facts.banned_admin):
        reason = DecisionReason.MEMBER_EVIDENCE_UNKNOWN
    elif facts.banned_admin:
        reason = DecisionReason.ADMIN_FULL_BANNED
    elif not facts.approved:
        reason = DecisionReason.NOT_APPROVED
    return AccessDecision(reason == DecisionReason.ALLOWED, reason)
