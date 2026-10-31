"""Standalone native-process proof; no pytest fixtures, bot container or worker event loop."""

import asyncio
import json
import sys
from os import _exit, fork, getpid, waitpid

from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageCharge, UsageEvent
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import DatabaseSettings

USER = 360001


def reject_inherited_pool(database: WorkerDatabase) -> None:
    """Reject an inherited adapter reference in a real prefork child."""
    policies = database.policies
    child = fork()
    if child == 0:
        try:
            policies.resolve(FEATURE_REGISTRY.all()[0].key, TriggerKind.DIRECT, USER)
        except ValueError:
            _exit(0)
        _exit(1)
    _, status = waitpid(child, 0)
    assert status == 0


def main() -> None:
    """Use only synthetic fixture data and return operational counters, never payloads."""
    assert "DrissionPage" not in sys.modules
    assert "hovorunv2.infrastructure.browser" not in sys.modules
    sys.modules["DrissionPage"] = None
    sys.modules["hovorunv2.infrastructure.browser"] = None
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
    sys.stdout.write(json.dumps({"pid": getpid(), "applied": applied.events_applied, "total": total}) + "\n")


if __name__ == "__main__":
    main()
