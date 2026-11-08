"""Standalone native-process proof; no pytest fixtures, bot container or worker event loop."""

import asyncio
import json
import os
import sys
import types
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec

from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageCharge, UsageEvent
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import DatabaseSettings

USER = 360001


class BlockedImportFinder(MetaPathFinder):
    """Prevent imports of specified modules in subprocess probe."""

    def __init__(self, *blocked: str) -> None:
        self._blocked = set(blocked)

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Raise ImportError if a blocked module is imported."""
        del path, target
        if fullname in self._blocked or any(fullname.startswith(f"{b}.") for b in self._blocked):
            msg = f"Import of blocked module '{fullname}' is forbidden"
            raise ImportError(msg)
        return None


def reject_inherited_pool(database: WorkerDatabase) -> None:
    """Reject an inherited adapter reference in a real prefork child."""
    policies = database.policies
    child = os.fork()
    if child == 0:
        try:
            policies.resolve(FEATURE_REGISTRY.all()[0].key, TriggerKind.DIRECT, USER)
        except ValueError:
            os._exit(0)
        os._exit(1)
    _, status = os.waitpid(child, 0)
    assert status == 0


def main() -> None:
    """Use only synthetic fixture data and return operational counters, never payloads."""
    assert "pydoll" not in sys.modules
    assert "hovorunv2.infrastructure.browser.async_browser" not in sys.modules
    sys.meta_path.insert(0, BlockedImportFinder("pydoll", "hovorunv2.infrastructure.browser.async_browser"))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        msg = "Native database probe must run without an event loop."
        raise AssertionError(msg)
    assert "hovorunv2.bootstrap.container" not in sys.modules
    subject = AccountingSubject(TargetKind.USER, USER)
    event = UsageEvent(sys.argv[2], subject, 90, (UsageCharge(MEDIA_BUCKET, 1),))
    config = DatabaseSettings(database_url=sys.argv[1], _env_file=None)
    with WorkerDatabase(config) as database:
        resolved = database.policies.resolve(FEATURE_REGISTRY.all()[0].key, TriggerKind.DIRECT, USER)
        assert resolved.subject.telegram_id == USER
        reject_inherited_pool(database)
        applied = database.ledger.apply_batch((event,), actor=ActorContext.system("usage_accounting"))
        total = database.ledger.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90)
    sys.stdout.write(json.dumps({"pid": os.getpid(), "applied": applied.events_applied, "total": total}) + "\n")


if __name__ == "__main__":
    main()
