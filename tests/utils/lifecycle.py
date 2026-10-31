"""Real native worker crash checkpoints for lifecycle recovery acceptance."""

import os
import secrets

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.jobs.admission import JobStage, SettlementIntent
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobOutcome
from hovorunv2.application.usage.budgets import BudgetRequest, RuntimeNamespace, UnitAmount
from hovorunv2.application.usage.policies import UsageSafetyCaps, UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageCharge, UsageEvent
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.config import Settings
from tests.utils.runtime import ManualClock


def crash_at_settlement_checkpoint(
    settings: Settings,
    namespace: RuntimeNamespace,
    envelope: JobEnvelope,
    checkpoint: str,
) -> None:
    """Exit a real child after intent, receipt, or runtime commit without running cleanup handlers."""
    clock = ManualClock(envelope.accepted_at)
    with (
        WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
        WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
    ):
        token = secrets.token_hex(16)
        assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), token) is not None
        usage = database.policies.resolve(envelope.feature, envelope.trigger, envelope.actor_id)
        amounts = (UnitAmount(UsageUnit.REQUESTS, 1),)
        request = BudgetRequest(envelope.job_id, usage, amounts, envelope.deadline)
        assert runtime.budgets.reserve(request, clock.now(), tuple(0 for _ in usage.policies)).allowed
        assert runtime.admission.mark_reserved(envelope.job_id, token) is not None
        assert runtime.admission.begin_delivery(envelope.job_id, token, clock.now()) is not None
        event = UsageEvent(
            envelope.job_id,
            AccountingSubject(envelope.charged_kind, envelope.charged_telegram_id),
            int(clock.now()),
            tuple(UsageCharge(policy.bucket, 1, policy.unit, policy.window) for policy in usage.policies),
        )
        intent = SettlementIntent(outcome=JobOutcome.COMPLETE, actual=amounts, event=event)
        assert runtime.admission.prepare_settlement(envelope.job_id, token, intent) is not None
        if checkpoint in {"receipt", "runtime"}:
            database.ledger.apply_batch((event,), actor=ActorContext.system(reason="synthetic completion"))
        if checkpoint == "runtime":
            runtime.budgets.finish(envelope.job_id, amounts, clock.now())
        os._exit(0)
