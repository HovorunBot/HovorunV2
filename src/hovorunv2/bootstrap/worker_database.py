"""Child-owned native database composition, independent of bot polling and browser imports."""

from os import getpid
from types import TracebackType
from typing import Self

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureRegistry
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.infrastructure.authorization.casbin_policy import load_packaged_policy
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.infrastructure.persistence.stores.worker_notification_store import (
    WorkerNotificationStore,
)
from hovorunv2.infrastructure.persistence.stores.worker_store import (
    WorkerOperationsStore,
    WorkerPolicyStore,
    WorkerUsageLedgerStore,
)

WORKER_POOL_SIZE = 1
WORKER_POOL_OVERFLOW = 0
DEFAULT_SAFETY = UsageSafetyCaps()


class WorkerDatabase:
    """Enter inside a worker child; construction before fork never creates a connection or pool.

    Tasks own sessions through the adapters. A child owns this context and disposes its engine
    during shutdown. R09 will connect this composition to real Celery lifecycle signals.
    """

    def __init__(
        self,
        config: DatabaseSettings,
        registry: FeatureRegistry = FEATURE_REGISTRY,
        safety: UsageSafetyCaps = DEFAULT_SAFETY,
    ) -> None:
        self._config = config
        self._registry = registry
        self._safety = safety
        self._opened = False
        self._engine: Engine | None = None
        self._owner: int | None = None

    def __enter__(self) -> Self:
        """Load native resources in the process using them; never import the bot container."""
        if self._opened:
            msg = "Worker database context cannot be entered more than once."
            raise ValueError(msg)
        policy = load_packaged_policy()
        engine = create_engine(
            self._config.database_url,
            pool_size=WORKER_POOL_SIZE,
            max_overflow=WORKER_POOL_OVERFLOW,
            pool_pre_ping=True,
            hide_parameters=True,
        )
        sessions = sessionmaker(engine, expire_on_commit=False)
        self._policies = WorkerPolicyStore(sessions, policy, self._registry, self._safety, self._require_owner)
        self._ledger = WorkerUsageLedgerStore(sessions, self._registry, self._require_owner)
        self._operations = WorkerOperationsStore(sessions, self._require_owner)
        self._notifications = WorkerNotificationStore(sessions, self._require_owner)
        self._opened = True
        self._engine = engine
        self._owner = getpid()
        return self

    def _require_owner(self) -> None:
        if self._engine is None or self._owner != getpid():
            msg = "Worker database resources require an open context in their owning process."
            raise ValueError(msg)

    def _require_open_engine(self) -> Engine:
        """Return the active engine or raise if closed or unowned."""
        self._require_owner()
        if self._engine is None:
            msg = "Worker database resources require an open context in their owning process."
            raise ValueError(msg)
        return self._engine

    @property
    def policies(self) -> WorkerPolicyStore:
        """Expose policy reads only to the owning child."""
        self._require_owner()
        return self._policies

    @property
    def ledger(self) -> WorkerUsageLedgerStore:
        """Expose accounting only to the owning child."""
        self._require_owner()
        return self._ledger

    @property
    def operations(self) -> WorkerOperationsStore:
        """Expose operations persistence only to the owning child."""
        self._require_owner()
        return self._operations

    @property
    def notifications(self) -> WorkerNotificationStore:
        """Expose notification delivery persistence only to the owning child."""
        self._require_owner()
        return self._notifications

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close all pooled connections in the process that created them."""
        engine = self._require_open_engine()
        engine.dispose()
        self._engine = None
        self._owner = None
