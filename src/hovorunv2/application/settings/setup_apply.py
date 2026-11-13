"""Atomic, conflict-aware, idempotent application of reviewed guided setup configurations."""

import secrets
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.settings.settings_session_service import SettingsTarget
from hovorunv2.application.settings.setup_drafts import (
    SetupConfigurationValues,
    SetupDraft,
    SetupDraftStorePort,
    SetupStep,
)
from hovorunv2.domain.enums import TargetKind


class SetupApplyOutcome(StrEnum):
    """Typed outcomes for atomic guided setup confirmation."""

    APPLIED = "applied"
    CONFLICT = "conflict"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


class SetupApplyRequest(BaseModel):
    """Validated request to apply a reviewed setup configuration in one transaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    target: SettingsTarget
    actor_id: int
    actor_generation: int
    transport_kind: str
    baseline: SetupConfigurationValues
    baseline_version: int
    values: SetupConfigurationValues


class SetupApplyResult(BaseModel):
    """Result returned by atomic guided setup confirmation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: SetupApplyOutcome
    applied_values: SetupConfigurationValues | None = None
    current_baseline: SetupConfigurationValues | None = None
    current_version: int | None = None
    settings_version: int | None = None
    idempotent_replay: bool = False


class SetupApplyStorePort(Protocol):
    """Transactional persistence contract for guided setup baseline reads and atomic apply."""

    async def load_baseline(self, chat_id: int) -> tuple[SetupConfigurationValues, int] | None:
        """Load the current effective configuration snapshot and settings_version for a chat."""
        ...

    async def apply_configuration(self, request: SetupApplyRequest) -> SetupApplyResult:
        """Persist reviewed configuration, audit event, and idempotency record in one transaction."""
        ...


class SetupApplyService:
    """Coordinate authority checks, atomic apply, conflict refresh, and draft slot release."""

    def __init__(
        self,
        apply_store: SetupApplyStorePort,
        draft_store: SetupDraftStorePort,
        group_management: GroupManagementService,
    ) -> None:
        self._apply_store = apply_store
        self._draft_store = draft_store
        self._group_management = group_management

    async def load_baseline(self, chat_id: int) -> tuple[SetupConfigurationValues, int] | None:
        """Return the current configuration baseline and revision for a canonical chat."""
        return await self._apply_store.load_baseline(chat_id)

    async def commit_draft(self, draft: SetupDraft, *, actor_id: int) -> SetupApplyResult:
        """Revalidate actor authority and atomically apply a reviewed setup draft."""
        if draft.owner_id != actor_id:
            return SetupApplyResult(outcome=SetupApplyOutcome.DENIED)
        perm_target = PermissionTarget(
            kind=TargetKind.GROUP.value,
            entity_id=draft.target.entity_id,
            generation=draft.target.generation,
            telegram_id=draft.target.telegram_id,
        )
        if not await self._group_management.can_manage(actor_id, perm_target):
            return SetupApplyResult(outcome=SetupApplyOutcome.DENIED)

        request = SetupApplyRequest(
            operation_id=draft.operation_id,
            target=draft.target,
            actor_id=actor_id,
            actor_generation=draft.owner_generation,
            transport_kind="group",
            baseline=draft.baseline,
            baseline_version=draft.baseline_version,
            values=draft.draft,
        )
        result = await self._apply_store.apply_configuration(request)
        if result.outcome == SetupApplyOutcome.APPLIED:
            await self._draft_store.release(draft.target.telegram_id, draft.session_id)
        elif (
            result.outcome == SetupApplyOutcome.CONFLICT
            and result.current_baseline is not None
            and result.current_version is not None
        ):
            refreshed = draft.model_copy(
                update={
                    "baseline": result.current_baseline,
                    "baseline_version": result.current_version,
                    "step": SetupStep.REVIEW,
                    "operation_id": secrets.token_hex(16),
                }
            )
            await self._draft_store.save(refreshed)
        return result
