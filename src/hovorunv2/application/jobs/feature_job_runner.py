"""Native synchronous Celery worker use case orchestrating ticket stage execution."""

import logging
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import replace

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.features.emergency import envelope_is_current
from hovorunv2.application.features.feature_policy import SyncPolicyPort
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureDefinition, FeatureRegistry
from hovorunv2.application.jobs.admission import (
    AdmissionConflictError,
    JobStage,
    SettlementIntent,
    SyncAdmissionPort,
    TicketState,
)
from hovorunv2.application.jobs.feature_jobs import (
    JobEnvelope,
    JobOutcome,
    LoadedPayload,
    PayloadState,
    SyncPayloadPort,
)
from hovorunv2.application.jobs.http_ports import (
    HTTPTransportError,
    SyncBrowserPort,
    SyncHTTPPort,
    SyncQueuePublisherPort,
    SyncTelegramDeliveryPort,
    TelegramDeliveryUncertainError,
    TelegramRejectedError,
)
from hovorunv2.application.jobs.worker_lifecycle_service import WorkerLifecycleService
from hovorunv2.application.media.errors import (
    BrowserContinuationRequired,
    BrowserError,
    MediaExtractionError,
)
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.finalization import append_translation, prepare_caption_text
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.providers.base import FACEBOOK_CONFIG, BaseMediaClient
from hovorunv2.application.media.providers.bluesky import BlueskyService
from hovorunv2.application.media.providers.instagram import InstagramService
from hovorunv2.application.media.providers.reddit import RedditService
from hovorunv2.application.media.providers.threads import ThreadsService
from hovorunv2.application.media.providers.tiktok import TikTokService
from hovorunv2.application.media.providers.twitter import TwitterService
from hovorunv2.application.media.providers.youtube import YoutubeService
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.budgets import (
    BudgetRequest,
    Clock,
    ReservationDecision,
    ReservationState,
    SyncBudgetPort,
    UnitAmount,
)
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, SyncLedgerPort, UsageCharge, UsageEvent
from hovorunv2.domain.enums import (
    PROVIDER_SPLIT_COMMANDS,
    SPLIT_TO_PROVIDER_COMMAND,
    CommandName,
    TargetKind,
)

logger = logging.getLogger(__name__)


def default_media_clients(settings: AppSettings | None = None) -> dict[CommandName, BaseMediaClient]:
    """Construct native synchronous provider clients for worker execution."""
    return {
        CommandName.TWITTER: TwitterService.sync(),
        CommandName.INSTAGRAM: InstagramService.sync(settings),
        CommandName.TIKTOK: TikTokService.sync(),
        CommandName.THREADS: ThreadsService.sync(),
        CommandName.BLUESKY: BlueskyService.sync(),
        CommandName.FACEBOOK: BaseMediaClient.for_worker(FACEBOOK_CONFIG),
        CommandName.YOUTUBE: YoutubeService.sync(),
        CommandName.REDDIT: RedditService.sync(),
    }


class FeatureJobRunner:
    """Synchronous worker use case executing feature jobs through child-local adapters."""

    def __init__(
        self,
        admission: SyncAdmissionPort,
        payloads: SyncPayloadPort,
        budgets: SyncBudgetPort,
        policies: SyncPolicyPort,
        ledger: SyncLedgerPort,
        http: SyncHTTPPort,
        delivery: SyncTelegramDeliveryPort,
        translation: NativeTranslationService,
        extractor: NativeMediaExtractor,
        clock: Clock,
        settings: AppSettings,
        publisher: SyncQueuePublisherPort,
        *,
        registry: FeatureRegistry = FEATURE_REGISTRY,
        browser: SyncBrowserPort | None = None,
        clients: Sequence[BaseMediaClient] | Mapping[CommandName, BaseMediaClient] | None = None,
    ) -> None:
        self._admission = admission
        self._payloads = payloads
        self._budgets = budgets
        self._policies = policies
        self._ledger = ledger
        self._http = http
        self._delivery = delivery
        self._translation = translation
        self._extractor = extractor
        self._clock = clock
        self._settings = settings
        self._registry = registry
        self._browser = browser
        self._publisher = publisher
        self._clients = self._init_clients(clients, settings)
        self._lifecycle = WorkerLifecycleService(admission, budgets, ledger, clock)

    def _init_clients(
        self,
        clients: Sequence[BaseMediaClient] | Mapping[CommandName, BaseMediaClient] | None,
        settings: AppSettings,
    ) -> dict[CommandName, BaseMediaClient]:
        if clients is None:
            return default_media_clients(settings)
        if isinstance(clients, Mapping):
            return dict(clients)
        return {client.name: client for client in clients}

    def run(self, envelope_data: dict[str, object], stage: JobStage = JobStage.MEDIA) -> str:
        """Catch request-bearing failures and return only an allowlisted safe terminal code."""
        try:
            claim_result = self._parse_and_claim(envelope_data, stage)
            if claim_result is None:
                return self._resolve_unclaimed(envelope_data)
            envelope, token = claim_result
            return self._execute_claimed(envelope, stage, token)
        except Exception:
            logger.warning("Feature job execution deferred")
            return JobOutcome.FAILED.value

    def _parse_and_claim(self, envelope_data: dict[str, object], stage: JobStage) -> tuple[JobEnvelope, str] | None:
        try:
            envelope = JobEnvelope.model_validate(envelope_data)
        except ValueError, TypeError:
            return None
        now = self._clock.now()
        if now >= envelope.deadline:
            self._finish_expired_unclaimed(envelope.job_id)
            return None
        token = secrets.token_hex(16)
        ticket = self._admission.claim_stage(envelope.job_id, stage, now, token, expected_envelope=envelope)
        if ticket is None:
            return None
        return envelope, token

    def _finish_expired_unclaimed(self, job_id: str) -> None:
        try:
            self._admission.finish(job_id, JobOutcome.EXPIRED)
        except Exception:
            logger.warning("Expired ticket reconciliation deferred")

    def _resolve_unclaimed(self, envelope_data: dict[str, object]) -> str:
        job_id = envelope_data.get("job_id")
        if not isinstance(job_id, str):
            return JobOutcome.FAILED.value
        ticket = self._admission.get(job_id)
        if ticket is None:
            return JobOutcome.FAILED.value
        if ticket.outcome is not None:
            return ticket.outcome.value
        if ticket.envelope.deadline <= self._clock.now():
            return JobOutcome.EXPIRED.value
        if ticket.state == TicketState.CANCELLING:
            return JobOutcome.CANCELLED.value
        return JobOutcome.FAILED.value

    def _execute_claimed(self, envelope: JobEnvelope, stage: JobStage, token: str) -> str:
        try:
            loaded = self._load_payload(envelope, token)
            if loaded is None or loaded.data is None:
                return self._handle_unavailable_payload(envelope.job_id)
            now = self._clock.now()
            reservation = self._reserve_execution_budget(envelope, now)
            if not reservation.allowed:
                return self._handle_denied_reservation(envelope, token, reservation)
            return self._execute_reserved(envelope, loaded, stage, token, now)
        except Exception:
            logger.exception("Claimed execution reconciliation deferred")
            return JobOutcome.FAILED.value

    def _execute_reserved(
        self, envelope: JobEnvelope, loaded: LoadedPayload, stage: JobStage, token: str, now: float
    ) -> str:
        """Fence cancellation and stale emergency epochs before extraction or delivery."""
        ticket = self._admission.mark_reserved(envelope.job_id, token)
        if ticket is None:
            return JobOutcome.FAILED.value
        if ticket.state == TicketState.CANCELLING:
            self._complete_zero(envelope.job_id, token, JobOutcome.CANCELLED)
            return JobOutcome.CANCELLED.value
        if not envelope_is_current(envelope.feature_epoch, self._policies.emergency_state(envelope.feature)):
            self._complete_zero(envelope.job_id, token, JobOutcome.CANCELLED)
            return JobOutcome.CANCELLED.value
        return self._extract_and_deliver(envelope, loaded, stage, token, now)

    def _load_payload(self, envelope: JobEnvelope, token: str) -> LoadedPayload | None:
        loaded = self._payloads.load(envelope.payload_ref, envelope.job_id)
        if loaded.state != PayloadState.READY or loaded.data is None:
            outcome = JobOutcome.EXPIRED if loaded.state == PayloadState.EXPIRED else JobOutcome.PAYLOAD_UNAVAILABLE
            self._finish_ticket_safe(envelope.job_id, token, outcome)
            return None
        return loaded

    def _handle_unavailable_payload(self, job_id: str) -> str:
        ticket = self._admission.get(job_id)
        if ticket is not None and ticket.outcome is not None:
            return ticket.outcome.value
        return JobOutcome.PAYLOAD_UNAVAILABLE.value

    def _reserve_execution_budget(self, envelope: JobEnvelope, now: float) -> ReservationDecision:
        group_id = envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None
        provider = self._resolve_provider_command(envelope.feature)
        feature_key = self._registry.capture(provider, is_video=True).key if provider is not None else envelope.feature
        usage = self._policies.resolve(feature_key, envelope.trigger, envelope.actor_id, group_id=group_id)
        if provider is not None:
            usage = replace(usage, policies=tuple(p for p in usage.policies if p.bucket == MEDIA_BUCKET))
        distinct_units = dict.fromkeys(policy.unit for policy in usage.policies)
        estimates = tuple(UnitAmount(unit, 1) for unit in distinct_units)
        request = BudgetRequest(
            reservation_id=envelope.job_id,
            usage=usage,
            estimates=estimates,
            deadline=envelope.deadline,
        )
        subject = AccountingSubject(usage.subject.kind, usage.subject.telegram_id)
        totals = tuple(
            self._ledger.total(subject, policy.bucket, policy.unit, policy.window, int(now))
            for policy in usage.policies
        )
        return self._budgets.reserve(request, now, totals)

    def _handle_denied_reservation(self, envelope: JobEnvelope, token: str, reservation: ReservationDecision) -> str:
        if reservation.state == ReservationState.WAITING:
            retry = reservation.next_eligible_at
            if retry is None:
                msg = "Quota waiting requires an eligibility time."
                raise ValueError(msg)
            if retry < envelope.deadline:
                deferred = self._admission.defer(envelope.job_id, token, retry)
                if deferred is None:
                    self._finish_ticket_safe(envelope.job_id, token, JobOutcome.CANCELLED)
                    return JobOutcome.CANCELLED.value
                return ReservationState.WAITING.value
        if reservation.state == ReservationState.UNAVAILABLE:
            # A lost reserve acknowledgement can hide a live reservation; do not refund it.
            return JobOutcome.FAILED.value
        outcome = (
            JobOutcome.EXPIRED
            if reservation.state in {ReservationState.EXPIRED, ReservationState.WAITING}
            else JobOutcome.FAILED
        )
        self._finish_ticket_safe(envelope.job_id, token, outcome)
        return outcome.value

    def _extract_and_deliver(
        self,
        envelope: JobEnvelope,
        loaded: LoadedPayload,
        stage: JobStage,
        token: str,
        now: float,
    ) -> str:
        assert loaded.data is not None
        try:
            raw_payload, client = self._extract_stage(envelope, loaded.data, stage, token)
        except BrowserContinuationRequired:
            return JobOutcome.COMPLETE.value
        except MediaExtractionError, BrowserError, HTTPTransportError:
            self._complete_zero(envelope.job_id, token, JobOutcome.FAILED)
            return JobOutcome.FAILED.value
        return self._finish_extraction(envelope, raw_payload, client, loaded.sender_label, token, now)

    def _finish_extraction(
        self,
        envelope: JobEnvelope,
        raw_payload: RichMediaPayload,
        client: BaseMediaClient,
        sender_label: str | None,
        token: str,
        now: float,
    ) -> str:
        split_def = self._check_post_extraction_gates(envelope, raw_payload, client)
        if split_def is None:
            self._complete_zero(envelope.job_id, token, JobOutcome.COMPLETE)
            return JobOutcome.COMPLETE.value
        return self._deliver_and_account(envelope, raw_payload, client, sender_label, split_def, token, now)

    def _deliver_and_account(
        self,
        envelope: JobEnvelope,
        raw_payload: RichMediaPayload,
        client: BaseMediaClient,
        sender_label: str | None,
        split_def: FeatureDefinition,
        token: str,
        now: float,
    ) -> str:
        self._translate_payload(raw_payload, envelope.target.telegram_id, strip_hashtags=client.config.strip_hashtags)
        if self._admission.begin_delivery(envelope.job_id, token, self._clock.now()) is None:
            self._complete_zero(envelope.job_id, token, JobOutcome.CANCELLED)
            return JobOutcome.CANCELLED.value
        try:
            self._delivery.deliver(
                envelope.target.telegram_id,
                envelope.message_id,
                raw_payload,
                client.platform_label,
                sender_label,
            )
        except TelegramDeliveryUncertainError:
            return TicketState.DELIVERY_UNCERTAIN.value
        except TelegramRejectedError:
            self._complete_zero(envelope.job_id, token, JobOutcome.FAILED)
            return JobOutcome.FAILED.value
        self._lifecycle.complete(envelope.job_id, token, self._settlement_intent(envelope, split_def, now))
        return JobOutcome.COMPLETE.value

    def _extract_stage(
        self, envelope: JobEnvelope, data: bytes, stage: JobStage, token: str
    ) -> tuple[RichMediaPayload, BaseMediaClient]:
        url = data.decode().splitlines()[0]
        client = self._find_client(envelope.feature, url)
        if client is None:
            msg = f"No media client found for feature {envelope.feature} or URL."
            raise MediaExtractionError(msg)
        if stage == JobStage.MEDIA:
            return self._extract_media_stage(envelope, url, client, token)
        return self._extract_browser_stage(url, client)

    def _extract_media_stage(
        self, envelope: JobEnvelope, url: str, client: BaseMediaClient, token: str
    ) -> tuple[RichMediaPayload, BaseMediaClient]:
        try:
            raw_payload = client.extract_media_sync(url, self._http, self._extractor)
        except BrowserContinuationRequired:
            self._handoff_to_browser(envelope, token)
            raise
        else:
            return raw_payload, client

    def _extract_browser_stage(self, url: str, client: BaseMediaClient) -> tuple[RichMediaPayload, BaseMediaClient]:
        if self._browser is None:
            msg = "Browser service is not available for browser stage."
            raise BrowserError(msg)
        raw_payload = client.extract_browser_media_sync(url, self._http, self._browser)
        return raw_payload, client

    def _handoff_to_browser(self, envelope: JobEnvelope, token: str) -> None:
        ready = self._admission.handoff_stage(envelope.job_id, token, JobStage.BROWSER)
        if ready is None:
            self._complete_zero(envelope.job_id, token, JobOutcome.CANCELLED)
            return
        self._publisher.publish_stage(envelope, JobStage.BROWSER)

    def _check_post_extraction_gates(
        self, envelope: JobEnvelope, raw_payload: RichMediaPayload, client: BaseMediaClient
    ) -> FeatureDefinition | None:
        target = PermissionTarget(
            kind=envelope.target.kind,
            entity_id=envelope.target.entity_id,
            generation=envelope.target.generation,
            telegram_id=envelope.target.telegram_id,
        )
        if not self._policies.active_target(target):
            return None
        provider_cmd = self._resolve_provider_command(envelope.feature, client)
        if provider_cmd is None:
            return None
        split_def = self._registry.capture(provider_cmd, is_video=raw_payload.is_video)
        decision = self._policies.evaluate(
            split_def.key,
            envelope.target.telegram_id,
            envelope.actor_id,
            trigger=envelope.trigger,
        )
        if not decision.allowed:
            return None
        return split_def

    def _translate_payload(self, payload: RichMediaPayload, chat_id: int, *, strip_hashtags: bool = False) -> None:
        if payload.content:
            payload.content = prepare_caption_text(payload.content, strip_hashtags=strip_hashtags)
            translated = self._translation.translate_if_needed(payload.content, chat_id)
            payload.content = append_translation(payload.content, translated)
        if payload.quoted_payload and payload.quoted_payload.content:
            payload.quoted_payload.content = prepare_caption_text(
                payload.quoted_payload.content, strip_hashtags=strip_hashtags
            )
            quote_translated = self._translation.translate_if_needed(payload.quoted_payload.content, chat_id)
            payload.quoted_payload.content = append_translation(payload.quoted_payload.content, quote_translated)

    def _settlement_intent(self, envelope: JobEnvelope, split_def: FeatureDefinition, now: float) -> SettlementIntent:
        group_id = envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None
        split_usage = self._policies.resolve(split_def.key, envelope.trigger, envelope.actor_id, group_id=group_id)
        charges = tuple(UsageCharge(policy.bucket, 1, policy.unit, policy.window) for policy in split_usage.policies)
        event = UsageEvent(
            event_id=envelope.job_id,
            subject=AccountingSubject(envelope.charged_kind, envelope.charged_telegram_id),
            occurred_at=int(now),
            charges=charges,
        )
        return SettlementIntent(outcome=JobOutcome.COMPLETE, actual=(UnitAmount(UsageUnit.REQUESTS, 1),), event=event)

    def _complete_zero(self, job_id: str, token: str, outcome: JobOutcome) -> None:
        self._lifecycle.complete(
            job_id, token, SettlementIntent(outcome=outcome, actual=(UnitAmount(UsageUnit.REQUESTS, 0),))
        )

    def recover(self) -> int:
        """Run one bounded fair waiting and settlement recovery sweep in the owning child."""
        return self._lifecycle.recover(self._publisher)

    def _finish_ticket_safe(self, job_id: str, token: str, outcome: JobOutcome) -> None:
        try:
            ticket = self._admission.get(job_id)
            if ticket is not None and ticket.budget_reserved:
                self._complete_zero(job_id, token, outcome)
                return
            self._admission.finish(job_id, outcome, token=token)
        except AdmissionConflictError:
            logger.warning("Admission conflict finishing ticket %s", job_id)

    def _find_client(self, feature: str, url: str) -> BaseMediaClient | None:
        provider = self._resolve_provider_command(feature)
        if provider is not None and provider in self._clients:
            return self._clients[provider]
        for client in self._clients.values():
            if client.pattern.search(url):
                return client
        return None

    def _resolve_provider_command(self, feature: str, client: BaseMediaClient | None = None) -> CommandName | None:
        if client is not None:
            return client.name
        try:
            cmd = CommandName(feature)
        except ValueError:
            return None
        if cmd in PROVIDER_SPLIT_COMMANDS:
            return cmd
        return SPLIT_TO_PROVIDER_COMMAND.get(cmd)
