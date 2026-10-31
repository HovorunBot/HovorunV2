"""Publish release updates as recipient events instead of direct broadcasts."""

from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.jobs.feature_jobs import PayloadState, SealedPayload
from hovorunv2.application.operations.changelog import get_changelog_updates, get_current_version, parse_version
from hovorunv2.application.operations.notification_delivery import NotificationLedgerPort
from hovorunv2.application.operations.notification_ledger import NotificationEvent, NotificationOutcome
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.application.operations.ports import WorkerOperationsPort
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext

BODY_TTL_SECONDS = 300
RELEASE_ACTOR = ActorContext.system("release notification")


class NotificationBodyPort(Protocol):
    """Synchronous encrypted body preparation for notification events."""

    def seal(self, job_id: str, data: bytes, deadline: float, *, sender_label: str | None = None) -> SealedPayload:
        """Encrypt a body without persisting it."""
        ...

    def put(self, payload: SealedPayload) -> PayloadState:
        """Store once under the original deadline and report a safe state."""
        ...


class ReleaseUpdateService:
    """Create one release event and per-group pending receipts; delivery stays separate."""

    def __init__(
        self,
        ops: WorkerOperationsPort,
        ledger: NotificationLedgerPort,
        bodies: NotificationBodyPort,
        settings: AppSettings,
        clock: Clock,
    ) -> None:
        self._ops = ops
        self._ledger = ledger
        self._bodies = bodies
        self._settings = settings
        self._clock = clock

    def publish(self) -> int:
        """Queue a release event for approved groups and advance the version marker."""
        current = get_current_version()
        last = self._ops.get_last_notified_version()
        if last is None:
            self._ops.set_last_notified_version(current)
            return 0
        if parse_version(current) <= parse_version(last):
            return 0
        updates = get_changelog_updates(last, current)
        if not updates:
            self._ops.set_last_notified_version(current)
            return 0
        event_key = f"release.{current.lstrip('v')}"
        sealed = self._bodies.seal(
            event_key, self._message(current, updates).encode(), self._clock.now() + BODY_TTL_SECONDS
        )
        if self._bodies.put(sealed) != PayloadState.READY:
            return 0
        event = NotificationEvent(event_key, NotificationCategory.RELEASE, "release", current, sealed.reference)
        self._ledger.ensure_event(event, actor=RELEASE_ACTOR)
        queued = 0
        for chat in self._ops.approved_group_chats():
            target = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
            self._ledger.record(
                event_key,
                target,
                NotificationOutcome.PENDING,
                attempts=0,
                telegram_message_id=None,
                actor=RELEASE_ACTOR,
            )
            queued += 1
        self._ops.set_last_notified_version(current)
        return queued

    def _message(self, version: str, updates: str) -> str:
        """Render the existing release message with the curated changelog text."""
        tag_name = f"v{version.lstrip('v')}"
        tag_url = f"{self._settings.github_url.rstrip('/')}/releases/tag/{tag_name}"
        return (
            f"🚀 <b>Hovorun Updated to {tag_name}</b>\n\n"
            f"{updates}\n\n"
            f'📖 <a href="{tag_url}">Read full release notes on GitHub</a>'
        )
