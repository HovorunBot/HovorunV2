"""Typed access administration commands and safe identity previews."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from hovorunv2.domain.enums import TargetKind


class AdminAction(StrEnum):
    """Access operations shared by private Telegram and offline CLI."""

    INSPECT = "inspect"
    TRUST = "trust"
    UNTRUST = "untrust"
    BAN = "ban"
    UNBAN = "unban"
    REQUESTS = "requests"
    APPROVE = "approve"
    ACCEPT = "accept"
    REJECT = "reject"
    BETA_GRANT = "beta-grant"
    BETA_REVOKE = "beta-revoke"


class AdminRequest(BaseModel):
    """Explicit operational fields; never a stored Telegram message or profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: AdminAction
    kind: TargetKind
    target: str
    reason: str | None = None
    public_explanation: str | None = None
    ban_kind: str = "full"
    feature: str = "*"
    request_id: int | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> AdminRequest:
        """Reject impossible scopes and incomplete decisions before showing confirmation."""
        if self.action in {AdminAction.TRUST, AdminAction.UNTRUST} and self.kind != TargetKind.USER:
            msg = "Trust applies only to users."
            raise ValueError(msg)
        if (
            self.action in {AdminAction.REQUESTS, AdminAction.APPROVE, AdminAction.ACCEPT, AdminAction.REJECT}
            and self.kind != TargetKind.GROUP
        ):
            msg = "Approval and request operations require a group."
            raise ValueError(msg)
        if self.action in {AdminAction.ACCEPT, AdminAction.REJECT} and (
            self.request_id is None or self.request_id <= 0
        ):
            msg = "Request decisions require a positive request ID."
            raise ValueError(msg)
        if self.action in {AdminAction.BAN, AdminAction.UNBAN} and (not self.reason or not self.reason.strip()):
            msg = "Ban changes require an internal reason."
            raise ValueError(msg)
        return self


class AdminTarget(BaseModel):
    """Immutable lifecycle identity displayed before a consequential change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TargetKind
    telegram_id: int
    entity_id: int
    generation: int
    username: str | None = None
