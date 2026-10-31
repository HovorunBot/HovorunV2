"""Typed access administration commands and safe identity previews."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from hovorunv2.application.usage.policies import UsageUnit
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
    LIMITS = "limits"
    LIMIT_SET = "limit-set"
    LIMIT_RESET = "limit-reset"


class AdminRequest(BaseModel):
    """Explicit operational fields; never a stored Telegram message or profile."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    action: AdminAction
    kind: TargetKind
    target: str
    reason: str | None = None
    public_explanation: str | None = None
    ban_kind: str = "full"
    feature: str = "*"
    request_id: int | None = None
    bucket: str | None = None
    unit: UsageUnit | None = None
    window_seconds: int | None = None
    limit: int | None = None
    burst: int | None = None
    concurrency: int | None = None
    pending: int | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> AdminRequest:
        """Reject impossible scopes and incomplete decisions before showing confirmation."""
        self._validate_action_scope()
        self._validate_request_decision()
        self._validate_ban_reason()
        self._validate_limit_reason()
        self._validate_limit_shape()
        return self

    def _validate_action_scope(self) -> None:
        """Restrict trust to users and approval/request operations to groups."""
        if self.action in {AdminAction.TRUST, AdminAction.UNTRUST} and self.kind != TargetKind.USER:
            msg = "Trust applies only to users."
            raise ValueError(msg)
        if (
            self.action in {AdminAction.REQUESTS, AdminAction.APPROVE, AdminAction.ACCEPT, AdminAction.REJECT}
            and self.kind != TargetKind.GROUP
        ):
            msg = "Approval and request operations require a group."
            raise ValueError(msg)

    def _validate_request_decision(self) -> None:
        """Require a concrete request only for operations that decide it."""
        if self.action not in {AdminAction.ACCEPT, AdminAction.REJECT}:
            return
        if self.request_id is None or self.request_id <= 0:
            msg = "Request decisions require a positive request ID."
            raise ValueError(msg)

    def _validate_ban_reason(self) -> None:
        """Require an operational reason only when changing a restriction."""
        if self.action not in {AdminAction.BAN, AdminAction.UNBAN}:
            return
        if not self.reason or not self.reason.strip():
            msg = "Ban changes require an internal reason."
            raise ValueError(msg)

    def _validate_limit_reason(self) -> None:
        """Require an operational reason only when changing a scoped limit override."""
        if self.action not in {AdminAction.LIMIT_SET, AdminAction.LIMIT_RESET}:
            return
        if not self.reason or not self.reason.strip():
            msg = "Limit changes require an internal reason."
            raise ValueError(msg)

    def _validate_limit_shape(self) -> None:
        """Require complete typed override dimensions before preview or mutation."""
        if self.action not in {AdminAction.LIMIT_SET, AdminAction.LIMIT_RESET}:
            return
        if not self.bucket or self.unit is None or self.window_seconds is None:
            msg = "Limit changes require bucket, unit and window."
            raise ValueError(msg)
        if self.action == AdminAction.LIMIT_SET and None in {
            self.limit,
            self.burst,
            self.concurrency,
            self.pending,
        }:
            msg = "Setting a limit requires limit, burst, concurrency and pending."
            raise ValueError(msg)


class AdminTarget(BaseModel):
    """Immutable lifecycle identity displayed before a consequential change."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    kind: TargetKind
    telegram_id: int
    entity_id: int
    generation: int
    username: str | None = None
