"""Child-owned synchronous Valkey resources and Celery native worker lifecycle wiring."""

import math
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from os import getpid
from types import TracebackType
from typing import Self

import redis
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.feature_jobs import FEATURE_JOB_FUNCTION, JobEnvelope
from hovorunv2.application.jobs.http_ports import SyncBrowserPort, SyncHTTPPort, SyncTelegramDeliveryPort
from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.operations.operations_runner import WorkerOperationsRunner
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace, WallClock
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.queues.feature_queue import (
    BROKER_SOCKET_SECONDS,
    BROKER_VISIBILITY_SECONDS,
    set_feature_task_runner,
    set_operations_runner,
    set_recovery_runner,
)
from hovorunv2.infrastructure.runtime.admission import WorkerAdmissionClient
from hovorunv2.infrastructure.runtime.budgets import WorkerBudgetClient
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from hovorunv2.interface.telegram.settings.prompts import SyncTelegramUnauthorizedPromptSender
from hovorunv2.interface.telegram.transport.sync_delivery import NativeTelegramAPI, NativeTelegramDelivery

RUNTIME_SOCKET_SECONDS = 2
DEFAULT_SAFETY = UsageSafetyCaps()


class WorkerRuntime:
    """Create clients only on entry inside a child and fence every retained adapter reference."""

    def __init__(self, settings: Settings, clock: Clock, namespace: RuntimeNamespace, safety: UsageSafetyCaps) -> None:
        self._settings = settings
        self._clock = clock
        self._namespace = namespace
        self._safety = safety
        self._client: redis.Redis | None = None
        self._owner: int | None = None
        self._opened = False

    def __enter__(self) -> Self:
        """Open a bounded synchronous connection pool in its owning process."""
        if self._opened:
            msg = "Worker runtime context cannot be entered more than once."
            raise ValueError(msg)
        client = redis.Redis.from_url(
            self._settings.valkey_url,
            decode_responses=True,
            max_connections=2,
            socket_connect_timeout=RUNTIME_SOCKET_SECONDS,
            socket_timeout=RUNTIME_SOCKET_SECONDS,
        )
        self._client = client
        self._owner = getpid()
        self._opened = True
        self._admission = WorkerAdmissionClient(client, self._namespace, self._require_owner)
        self._payloads = WorkerPayloadClient(
            client, self._settings, self._clock, self._namespace, self._safety, self._require_owner
        )
        self._budgets = WorkerBudgetClient(client, self._namespace, self._require_owner)
        return self

    def _require_owner(self) -> None:
        if self._client is None or self._owner != getpid():
            msg = "Worker runtime resources require an open context in their owning process."
            raise ValueError(msg)

    def _require_open_client(self) -> redis.Redis:
        """Return the active Valkey client or raise if closed or unowned."""
        self._require_owner()
        if self._client is None:
            msg = "Worker runtime resources require an open context in their owning process."
            raise ValueError(msg)
        return self._client

    @property
    def client(self) -> redis.Redis:
        """Expose open Valkey client to the owning child."""
        return self._require_open_client()

    @property
    def admission(self) -> WorkerAdmissionClient:
        """Expose lifecycle operations to the owning child."""
        self._require_owner()
        return self._admission

    @property
    def payloads(self) -> WorkerPayloadClient:
        """Expose encrypted payload access to the owning child."""
        self._require_owner()
        return self._payloads

    @property
    def budgets(self) -> WorkerBudgetClient:
        """Expose execution reservations to the owning child."""
        self._require_owner()
        return self._budgets

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close pooled connections and invalidate retained adapters."""
        client = self._require_open_client()
        client.close()
        self._client = None
        self._owner = None


class WorkerQueuePublisher:
    """Publish stage continuations back to Celery queues without importing bot queues."""

    def __init__(self, broker_url: str, namespace: RuntimeNamespace, safety: UsageSafetyCaps, clock: Clock) -> None:
        self._clock = clock
        self._safety = safety
        self._media_queue = namespace.value + "media"
        self._browser_queue = namespace.value + "browser"
        self._app = Celery("worker_publisher", broker=broker_url)
        self._app.conf.update(
            broker_connection_timeout=BROKER_SOCKET_SECONDS,
            broker_transport_options={
                "global_keyprefix": namespace.value,
                "visibility_timeout": BROKER_VISIBILITY_SECONDS,
                "socket_connect_timeout": BROKER_SOCKET_SECONDS,
                "socket_timeout": BROKER_SOCKET_SECONDS,
                "retry_policy": {"max_retries": 0},
            },
            task_publish_retry=False,
        )

    def publish_stage(self, envelope: JobEnvelope, stage: JobStage, not_before: float = 0) -> bool:
        """Send a task to the appropriate Celery queue for the target stage."""
        remaining = envelope.deadline - max(self._clock.now(), not_before)
        if remaining <= 0:
            return False
        queue = self._browser_queue if stage == JobStage.BROWSER else self._media_queue
        with self._app.connection_for_write() as connection, self._app.amqp.Producer(connection) as producer:
            self._app.send_task(
                FEATURE_JOB_FUNCTION,
                kwargs={"envelope": envelope.model_dump(mode="json"), "stage": stage.value},
                task_id=envelope.job_id,
                producer=producer,
                queue=queue,
                eta=datetime.fromtimestamp(not_before, UTC) if not_before else None,
                expires=datetime.fromtimestamp(envelope.deadline, UTC),
                time_limit=max(1, math.ceil(min(self._safety.execution_seconds, remaining))),
                ignore_result=True,
                retry=False,
                argsrepr="()",
                kwargsrepr="<feature envelope>",
            )
        return True

    def close(self) -> None:
        """Close publisher connection pool."""
        with suppress(Exception):
            self._app.close()


def _create_native_browser() -> SyncBrowserPort:
    from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService  # noqa: PLC0415

    return NativeBrowserService()


class LazyBrowserService:
    """Initialize Chromium only when browser extraction is genuinely requested."""

    def __init__(self, factory: Callable[[], SyncBrowserPort] = _create_native_browser) -> None:
        self._factory = factory
        self._service: SyncBrowserPort | None = None

    def _require(self) -> SyncBrowserPort:
        if self._service is None:
            self._service = self._factory()
        return self._service

    def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch rendered HTML using child-owned browser."""
        return self._require().get_content(url, wait_selector)

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Run extraction and download in one synchronous browser session."""
        return self._require().extract_and_download(url, extractor_fn, wait_selector, cookies)

    def close(self) -> None:
        """Dispose child-owned browser process if active."""
        if self._service is not None:
            self._service.close()
            self._service = None


class WorkerChildContext:
    """Manage lifecycle of all per-child synchronous resources."""

    def __init__(
        self,
        settings: Settings | None = None,
        namespace: RuntimeNamespace | None = None,
        safety: UsageSafetyCaps | None = None,
        clock: Clock | None = None,
        *,
        runner: FeatureJobRunner | None = None,
        delivery: SyncTelegramDeliveryPort | None = None,
        http: SyncHTTPPort | None = None,
        browser: SyncBrowserPort | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._namespace = namespace or RuntimeNamespace()
        self._safety = safety or DEFAULT_SAFETY
        self._clock = clock or WallClock()
        self._runner = runner
        self._custom_delivery = delivery
        self._custom_http = http
        self._custom_browser = browser
        self._db: WorkerDatabase | None = None
        self._runtime: WorkerRuntime | None = None
        self._owned_http: NativeHTTPClient | None = None
        self._http: SyncHTTPPort | None = None
        self._browser: SyncBrowserPort | None = None
        self._publisher: WorkerQueuePublisher | None = None
        self._opened = runner is not None

    def open(self) -> Self:
        """Open all child resources in the current process."""
        if self._opened:
            return self
        self._db = WorkerDatabase(self._settings, FEATURE_REGISTRY, self._safety)
        self._db.__enter__()
        self._runtime = WorkerRuntime(self._settings, self._clock, self._namespace, self._safety)
        self._runtime.__enter__()
        if self._custom_http is not None:
            self._http = self._custom_http
        else:
            self._owned_http = NativeHTTPClient()
            self._owned_http.__enter__()
            self._http = self._owned_http
        api = NativeTelegramAPI(self._http, self._settings.bot_token)
        downloader = NativeMediaDownloader(self._http)
        delivery = self._custom_delivery or NativeTelegramDelivery(api, downloader)
        translation = NativeTranslationService(self._http, self._settings, self._db.policies)
        extractor = NativeMediaExtractor()
        self._browser = self._custom_browser or LazyBrowserService()
        self._publisher = WorkerQueuePublisher(self._settings.valkey_url, self._namespace, self._safety, self._clock)
        self._runner = FeatureJobRunner(
            admission=self._runtime.admission,
            payloads=self._runtime.payloads,
            budgets=self._runtime.budgets,
            policies=self._db.policies,
            ledger=self._db.ledger,
            http=self._http,
            delivery=delivery,
            translation=translation,
            extractor=extractor,
            clock=self._clock,
            settings=self._settings,
            registry=FEATURE_REGISTRY,
            browser=self._browser,
            publisher=self._publisher,
        )
        prompt_sender = SyncTelegramUnauthorizedPromptSender(api)
        self._operations = WorkerOperationsRunner(
            ops=self._db.operations,
            client=self._runtime.client,
            namespace=self._namespace,
            api=api,
            prompt_sender=prompt_sender,
            settings=self._settings,
            clock=self._clock,
        )
        self._opened = True
        return self

    @property
    def opened(self) -> bool:
        """Return whether this child context has been opened."""
        return self._opened

    @property
    def runner(self) -> FeatureJobRunner:
        """Expose initialized runner to worker task execution."""
        if self._runner is None:
            msg = "Worker child context has not been opened."
            raise ValueError(msg)
        return self._runner

    @property
    def operations(self) -> WorkerOperationsRunner:
        """Expose initialized operations runner to maintenance task execution."""
        if self._operations is None:
            msg = "Worker child context has not been opened."
            raise ValueError(msg)
        return self._operations

    def close(self) -> None:
        """Close all child resources deterministically."""
        if not self._opened:
            return
        self._operations = None
        self._runner = None
        if self._publisher is not None:
            self._publisher.close()
            self._publisher = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._owned_http is not None:
            self._owned_http.close()
            self._owned_http = None
        self._http = None
        if self._runtime is not None:
            self._runtime.__exit__(None, None, None)
            self._runtime = None
        if self._db is not None:
            self._db.__exit__(None, None, None)
            self._db = None
        self._opened = False


_current_worker_context: WorkerChildContext | None = None


def init_worker_child(
    settings: Settings | None = None,
    namespace: RuntimeNamespace | None = None,
    safety: UsageSafetyCaps | None = None,
) -> WorkerChildContext:
    """Initialize resources for the current worker child process."""
    global _current_worker_context  # noqa: PLW0603
    if _current_worker_context is not None:
        return _current_worker_context
    context = WorkerChildContext(settings, namespace, safety)
    context.open()
    _current_worker_context = context
    set_feature_task_runner(_execute_task_in_child)
    set_recovery_runner(context.runner.recover)
    set_operations_runner(context.operations)
    return context


def _execute_task_in_child(envelope: dict[str, object], stage: JobStage) -> str:
    return get_worker_runner().run(envelope, stage)


def shutdown_worker_child() -> None:
    """Tear down resources when the worker child process exits."""
    global _current_worker_context  # noqa: PLW0603
    if _current_worker_context is not None:
        _current_worker_context.close()
        _current_worker_context = None
    set_feature_task_runner(None)
    set_recovery_runner(None)
    set_operations_runner(None)


def get_worker_runner() -> FeatureJobRunner:
    """Return the active FeatureJobRunner for the current child process."""
    if _current_worker_context is None:
        init_worker_child()
    assert _current_worker_context is not None
    return _current_worker_context.runner


def get_worker_operations_runner() -> WorkerOperationsRunner:
    """Return the active WorkerOperationsRunner for the current child process."""
    if _current_worker_context is None:
        init_worker_child()
    assert _current_worker_context is not None
    return _current_worker_context.operations


def set_worker_runner(runner: FeatureJobRunner | None) -> None:
    """Explicitly set or clear the runner for testing."""
    global _current_worker_context  # noqa: PLW0603
    if runner is None:
        shutdown_worker_child()
    else:
        if _current_worker_context is not None:
            _current_worker_context.close()
        _current_worker_context = WorkerChildContext(runner=runner)
        set_feature_task_runner(_execute_task_in_child)
        set_recovery_runner(runner.recover)
        set_operations_runner(None)


@contextmanager
def worker_child_context(
    settings: Settings | None = None,
    namespace: RuntimeNamespace | None = None,
    safety: UsageSafetyCaps | None = None,
    clock: Clock | None = None,
    runner: FeatureJobRunner | None = None,
    *,
    delivery: SyncTelegramDeliveryPort | None = None,
    http: SyncHTTPPort | None = None,
    browser: SyncBrowserPort | None = None,
) -> Generator[FeatureJobRunner]:
    """Scoped context manager for child-local runner lifecycle."""
    global _current_worker_context  # noqa: PLW0603
    old_context = _current_worker_context
    context = (
        WorkerChildContext(runner=runner)
        if runner is not None
        else WorkerChildContext(settings, namespace, safety, clock, delivery=delivery, http=http, browser=browser)
    )
    if runner is None:
        context.open()
    _current_worker_context = context
    set_feature_task_runner(_execute_task_in_child)
    set_recovery_runner(context.runner.recover)
    set_operations_runner(context.operations if runner is None else None)
    try:
        yield context.runner
    finally:
        context.close()
        _current_worker_context = old_context
        if old_context is not None:
            set_feature_task_runner(_execute_task_in_child)
            set_recovery_runner(old_context.runner.recover)
            set_operations_runner(old_context.operations if old_context.opened else None)
        else:
            set_feature_task_runner(None)
            set_recovery_runner(None)
            set_operations_runner(None)


@worker_process_init.connect
def on_worker_process_init(**kwargs: object) -> None:
    """Create child-local resources when Celery forks a worker child process."""
    init_worker_child()


@worker_process_shutdown.connect
def on_worker_process_shutdown(**kwargs: object) -> None:
    """Close child-local resources when Celery shuts down a worker child process."""
    shutdown_worker_child()
