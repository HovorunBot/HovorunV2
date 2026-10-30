"""Child-owned synchronous Valkey resources for admission, payloads and execution budgets."""

from os import getpid
from types import TracebackType
from typing import Self

import redis

from hovorunv2.application.budgets import Clock, RuntimeNamespace
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.infrastructure.admission import WorkerAdmissionClient
from hovorunv2.infrastructure.budgets import WorkerBudgetClient
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import WorkerPayloadClient

RUNTIME_SOCKET_SECONDS = 2


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
