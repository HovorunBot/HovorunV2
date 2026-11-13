"""Read live job cancellation and identity facts during native translation."""

from dataclasses import dataclass

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.feature_policy import SyncPolicyPort
from hovorunv2.application.jobs.admission import SyncAdmissionPort, TicketState
from hovorunv2.application.jobs.feature_jobs import JobEnvelope
from hovorunv2.application.translation.ports import CancellationToken


@dataclass(frozen=True)
class TranslationJobCancellation(CancellationToken):
    """Fence translation against current job ownership, emergency state and identity generation."""

    admission: SyncAdmissionPort
    policies: SyncPolicyPort
    envelope: JobEnvelope

    def is_cancelled(self) -> bool:
        """Return whether the original job no longer permits translation work."""
        ticket = self.admission.get(self.envelope.job_id)
        if ticket is None or ticket.state != TicketState.RUNNING:
            return True
        state = self.policies.emergency_state(self.envelope.feature)
        if state.disabled or state.epoch != self.envelope.feature_epoch:
            return True
        target = self.envelope.target
        if not self.policies.active_target(
            PermissionTarget(
                target.kind,
                target.entity_id,
                target.generation,
                target.telegram_id,
            )
        ):
            return True
        return not self.policies.evaluate(
            self.envelope.feature,
            target.telegram_id,
            self.envelope.actor_id,
            trigger=self.envelope.trigger,
        ).allowed
