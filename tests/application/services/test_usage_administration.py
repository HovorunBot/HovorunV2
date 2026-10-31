"""Audited scoped limit inspection and overrides through the shared administration boundary."""

import time

import pytest
from dishka import AsyncContainer

from hovorunv2.application.access.administration import AdminAction, AdminRequest
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import (
    AccountingSubject,
    UsageCharge,
    UsageEvent,
    UsageLedgerStore,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.interface.access_arguments import parse_access
from tests.conftest import provision_role
from tests.utils.usage import USER, usage_subjects

FEATURE = FEATURE_REGISTRY.all()[0].key
ADMIN = 360010
WINDOW_SECONDS = 60


def _request(action: AdminAction, *, target: int = USER) -> AdminRequest:
    """Build a complete typed request for the selected target and action."""
    kind = TargetKind.GROUP if target < 0 else TargetKind.USER
    values: dict[str, object] = {
        "action": action,
        "kind": kind,
        "target": str(target),
        "feature": FEATURE,
    }
    if action in {AdminAction.LIMIT_SET, AdminAction.LIMIT_RESET}:
        values.update(
            reason="synthetic limit change",
            bucket=MEDIA_BUCKET,
            unit=UsageUnit.REQUESTS,
            window_seconds=WINDOW_SECONDS,
        )
    if action == AdminAction.LIMIT_SET:
        values.update(limit=8, burst=4, concurrency=2, pending=10)
    return AdminRequest.model_validate(values)


async def _service(container: AsyncContainer) -> AdministrationService:
    """Provision a synthetic administrator and the target identities through real stores."""
    await usage_subjects(container)
    await provision_role(container, ADMIN)
    return await container.get(AdministrationService)


@pytest.mark.parametrize(("origin", "transport"), [("ordinary", "private"), ("telegram", "group"), ("system", None)])
async def test_limit_mutations_require_current_private_authority(
    test_container: AsyncContainer, origin: str, transport: str | None
) -> None:
    """Unrelated, group-transport and system callers cannot change scoped limits."""
    service = await _service(test_container)
    actor = (
        ActorContext.system() if origin == "system" else ActorContext.telegram(ADMIN if origin == "telegram" else USER)
    )
    target = await service.resolve(TargetKind.USER, str(USER))
    for action in (AdminAction.LIMIT_SET, AdminAction.LIMIT_RESET, AdminAction.LIMITS):
        with pytest.raises(PermissionError):
            await service.execute(_request(action), target, actor, transport)


async def test_limit_inspect_reports_default_source_and_independent_safety(test_container: AsyncContainer) -> None:
    """Inspection distinguishes defaults from overrides and exposes safety caps unchanged."""
    service = await _service(test_container)
    actor = ActorContext.telegram(ADMIN)
    target = await service.resolve(TargetKind.USER, str(USER))
    before = await service.execute(_request(AdminAction.LIMITS), target, actor, "private")
    assert {policy["source"] for policy in before["policies"]} == {"default"}
    safety = before["safety"]
    assert safety["global_concurrency"] == 2
    assert safety["global_pending"] == 500
    assert safety["payload_bytes"] == 64 * 1024
    assert await service.execute(_request(AdminAction.LIMIT_SET), target, actor, "private") == {"changed": True}
    after = await service.execute(_request(AdminAction.LIMITS), target, actor, "private")
    sources = {policy["bucket"]: policy["source"] for policy in after["policies"]}
    assert sources[MEDIA_BUCKET] == "override"
    assert sources[f"feature:{FEATURE}"] == "default"
    assert after["safety"] == safety


async def test_limit_reset_restores_default_without_resetting_consumption(
    test_container: AsyncContainer,
) -> None:
    """Removing an override returns to the default while current-window usage stays accounted."""
    service = await _service(test_container)
    ledger = await test_container.get(UsageLedgerStore)
    observed = (int(time.time()) // WINDOW_SECONDS) * WINDOW_SECONDS
    subject = AccountingSubject(TargetKind.USER, USER)
    event = UsageEvent("limit-admin-event", subject, observed, (UsageCharge(MEDIA_BUCKET, 1),))
    await ledger.apply_batch((event,), actor=ActorContext.system())
    window = UsageWindow(WINDOW_SECONDS)
    assert await ledger.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, window, observed) == 1
    actor = ActorContext.telegram(ADMIN)
    target = await service.resolve(TargetKind.USER, str(USER))
    assert await service.execute(_request(AdminAction.LIMIT_SET), target, actor, "private") == {"changed": True}
    assert await service.execute(_request(AdminAction.LIMIT_RESET), target, actor, "private") == {"changed": True}
    after = await service.execute(_request(AdminAction.LIMITS), target, actor, "private")
    assert {policy["source"] for policy in after["policies"]} == {"default"}
    assert await ledger.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, window, observed) == 1


def test_cli_accepts_typed_limit_arguments() -> None:
    """Offline and private parsers accept explicit typed override dimensions."""
    request = parse_access(
        [
            "limit-set",
            "user",
            str(USER),
            "--reason",
            "synthetic",
            "--bucket",
            MEDIA_BUCKET,
            "--unit",
            "requests",
            "--window-seconds",
            str(WINDOW_SECONDS),
            "--limit",
            "8",
            "--burst",
            "4",
            "--concurrency",
            "2",
            "--pending",
            "10",
        ]
    )
    assert request.action == AdminAction.LIMIT_SET
    assert request.unit == UsageUnit.REQUESTS
    assert (request.limit, request.burst, request.concurrency, request.pending) == (8, 4, 2, 10)
    inspect = parse_access(["limits", "user", str(USER), "--feature", FEATURE])
    assert inspect.action == AdminAction.LIMITS
    with pytest.raises(ValueError, match="bucket, unit and window"):
        parse_access(["limit-reset", "user", str(USER), "--reason", "synthetic"])
