"""Route native worker errors from parent and child processes to current owners."""

import html
import logging
import re
import sys
from copy import copy
from os import getpid

from celery.signals import after_setup_logger, after_setup_task_logger, task_received, worker_ready
from celery.worker.request import Request
from sqlalchemy.engine import make_url

from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_jobs import FEATURE_JOB_FUNCTION, JobEnvelope
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.infrastructure.config import DatabaseSettings, ErrorLoggingSettings, Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.logger import LOG_FORMAT, LogContextFilter, PrivacyFormatter
from hovorunv2.interface.telegram.transport.sync_delivery import NativeTelegramAPI, TelegramMethod
from hovorunv2.utils.job_logging import reset_job_log_context, set_job_log_context

ALERT_TIMEOUT_SECONDS = 3
ALERT_TEXT_LIMIT = 1500
HARD_TIMEOUT_TEMPLATE = "Hard time limit (%ss) exceeded for %s[%s]"
JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
logger = logging.getLogger(__name__)


class WorkerTelegramHandler(logging.Handler):
    """Send sanitized errors with short-lived resources owned by the emitting process."""

    def __init__(self, settings: Settings, *, api_base: str = "https://api.telegram.org") -> None:
        super().__init__(logging.ERROR)
        self._settings = settings
        self._api_base = api_base
        self._sending_process: int | None = None
        self.addFilter(LogContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        """Resolve current owners and send a bounded alert without recursive logging."""
        if self._sending_process == getpid():
            return
        self._sending_process = getpid()
        try:
            text = self._alert_text(record)
            self._send(text)
        except Exception as error:
            sys.stderr.write(f"Worker error alert could not be delivered ({type(error).__name__}).\n")
        finally:
            self._sending_process = None

    def _alert_text(self, record: logging.LogRecord) -> str:
        safe = copy(record)
        if record.name == "celery.worker.request" and record.msg == HARD_TIMEOUT_TEMPLATE:
            safe.name = __name__
            safe.msg = "Worker hard time limit exceeded"
            safe.args = ()
            return self._hard_timeout_alert(safe, record)
        return self._format_alert(safe)

    def _hard_timeout_alert(self, safe: logging.LogRecord, record: logging.LogRecord) -> str:
        job_id = "-"
        match record.args:
            case (_, _, str() as candidate) if JOB_ID_PATTERN.fullmatch(candidate):
                job_id = candidate
        tokens = set_job_log_context(job_id, "-")
        try:
            return self._format_alert(safe)
        finally:
            reset_job_log_context(tokens)

    def _format_alert(self, record: logging.LogRecord) -> str:
        formatted = PrivacyFormatter(LOG_FORMAT).format(record)
        return "🚨 <b>Worker Log Alert</b>\n\n<pre>" + html.escape(formatted[:ALERT_TEXT_LIMIT]) + "</pre>"

    def _send(self, text: str) -> None:
        url = make_url(self._settings.database_url).update_query_dict({"connect_timeout": str(ALERT_TIMEOUT_SECONDS)})
        database_settings = DatabaseSettings(database_url=url.render_as_string(hide_password=False), _env_file=None)
        with WorkerDatabase(database_settings) as database:
            owners = tuple(owner.telegram_id for owner in database.operations.list_owners())
        if not owners:
            sys.stderr.write("Worker error alert skipped: no current owners.\n")
            return
        with NativeHTTPClient(
            connect_timeout=ALERT_TIMEOUT_SECONDS,
            read_timeout=ALERT_TIMEOUT_SECONDS,
            total_timeout=ALERT_TIMEOUT_SECONDS,
        ) as http:
            api = NativeTelegramAPI(http, self._settings.bot_token, api_base=self._api_base)
            for owner_id in owners:
                try:
                    api.call(TelegramMethod.SEND_MESSAGE, {"chat_id": owner_id, "text": text, "parse_mode": "HTML"})
                except Exception as error:
                    sys.stderr.write(
                        f"Worker error alert could not be delivered to an owner ({type(error).__name__}).\n"
                    )


@after_setup_logger.connect
@after_setup_task_logger.connect
def configure_worker_logging(logger: logging.Logger, **kwargs: object) -> None:
    """Sanitize Celery sinks and attach synchronous owner routing when enabled."""
    for handler in logger.handlers:
        handler.setFormatter(PrivacyFormatter(LOG_FORMAT))
        handler.addFilter(LogContextFilter())
    if not ErrorLoggingSettings().error_dm_enabled:
        return
    if any(isinstance(handler, WorkerTelegramHandler) for handler in logger.handlers):
        return
    logger.addHandler(WorkerTelegramHandler(Settings()))


@task_received.connect
def log_received_feature_task(request: Request, **kwargs: object) -> None:
    """Record parent receipt before a prefork child claims the feature task."""
    if request.name not in {FEATURE_JOB_FUNCTION, "run_media_job", "run_browser_job"}:
        return
    try:
        envelope = JobEnvelope.model_validate(request.kwargs.get("envelope"))
        default_stage = JobStage.BROWSER if request.name == "run_browser_job" else JobStage.MEDIA
        stage = JobStage(request.kwargs.get("stage", default_stage))
    except ValueError, TypeError:
        logger.exception("Worker parent received an invalid feature task")
        return
    tokens = set_job_log_context(envelope.job_id, stage.value)
    try:
        logger.info("Worker parent received feature task")
    finally:
        reset_job_log_context(tokens)


@worker_ready.connect
def log_worker_ready(**kwargs: object) -> None:
    """Record that the parent completed startup and is ready to consume tasks."""
    logger.info("Worker parent ready to receive tasks")
