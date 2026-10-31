"""Celery application for independently deployed native worker and Beat containers."""

from celery import Celery
from kombu import Queue

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.bootstrap import worker_runtime  # noqa: F401  # Registers child lifecycle listeners.
from hovorunv2.infrastructure.config import BrokerSettings
from hovorunv2.infrastructure.queues.feature_queue import (
    BROKER_SOCKET_SECONDS,
    BROKER_VISIBILITY_SECONDS,
    build_beat_schedule,
    register_feature_tasks,
)

WORKER_APP_NAME = "hovorun_workers"
MEDIA_QUEUE_NAME = "media"
BROWSER_QUEUE_NAME = "browser"
OPERATIONS_QUEUE_NAME = "operations"


def build_worker_application(settings: BrokerSettings, namespace: RuntimeNamespace) -> Celery:
    """Build the shared native worker and Beat application without opening worker resources.

    Args:
        settings: Broker connection settings.
        namespace: Runtime namespace prefix isolating queue names and broker keys.

    Returns:
        Configured Celery application with feature tasks registered.
    """
    media_queue = namespace.value + MEDIA_QUEUE_NAME
    browser_queue = namespace.value + BROWSER_QUEUE_NAME
    operations_queue = namespace.value + OPERATIONS_QUEUE_NAME
    app = Celery(WORKER_APP_NAME, broker=settings.valkey_url)
    app.conf.update(
        task_default_queue=media_queue,
        task_queues=[Queue(media_queue), Queue(browser_queue), Queue(operations_queue)],
        beat_schedule=build_beat_schedule(namespace.value),
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_send_sent_event=False,
        worker_send_task_events=False,
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        worker_cancel_long_running_tasks_on_connection_loss=False,
        worker_prefetch_multiplier=1,
        broker_connection_timeout=BROKER_SOCKET_SECONDS,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "global_keyprefix": namespace.value,
            "visibility_timeout": BROKER_VISIBILITY_SECONDS,
            "socket_connect_timeout": BROKER_SOCKET_SECONDS,
            "socket_timeout": BROKER_SOCKET_SECONDS,
            "retry_policy": {"max_retries": 0},
        },
    )
    register_feature_tasks(app)
    return app


app = build_worker_application(BrokerSettings(), RuntimeNamespace())
