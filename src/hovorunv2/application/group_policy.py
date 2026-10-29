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


def _requires_administrator_verification(facts: GroupAccessFacts) -> bool:
    """Require fresh evidence; a proven administrator ban outranks unknown peers."""
    if not facts.admins_fresh:
        return True
    return facts.unknown_admin and not facts.banned_admin


def group_access(facts: GroupAccessFacts) -> AccessDecision:
    """Preserve absolute bans, fresh administrator evidence and durable approval order."""
    if not facts.active:
        return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)
    if facts.banned:
        return AccessDecision(allowed=False, reason=DecisionReason.GROUP_BANNED)
    if _requires_administrator_verification(facts):
        return AccessDecision(allowed=False, reason=DecisionReason.MEMBER_EVIDENCE_UNKNOWN)
    if facts.banned_admin:
        return AccessDecision(allowed=False, reason=DecisionReason.ADMIN_FULL_BANNED)
    if not facts.approved:
        return AccessDecision(allowed=False, reason=DecisionReason.NOT_APPROVED)
    return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)
