"""Standalone native Celery worker process proof with zero warnings under -W error."""

import asyncio
import json
import logging
import sys
import traceback
from os import _exit, fork, getpid, waitpid

from hovorunv2.application.admission import JobStage
from hovorunv2.application.budgets import RuntimeNamespace
from hovorunv2.application.feature_jobs import JobOutcome
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.bootstrap.worker_runtime import (
    get_worker_runner,
    init_worker_child,
    shutdown_worker_child,
)
from hovorunv2.infrastructure.config import Settings


def main() -> None:
    """Verify clean child execution without event loops, container imports, or Chromium in media queue."""
    logging.disable(logging.CRITICAL)
    assert "DrissionPage" not in sys.modules
    assert "hovorunv2.infrastructure.browser" not in sys.modules
    sys.modules["DrissionPage"] = None
    sys.modules["hovorunv2.infrastructure.browser"] = None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        msg = "Native Celery worker must run without an event loop."
        raise AssertionError(msg)
    assert "hovorunv2.bootstrap.container" not in sys.modules

    db_url = sys.argv[1]
    valkey_url = sys.argv[2]
    namespace = RuntimeNamespace(sys.argv[3])
    envelope_json = json.loads(sys.argv[4])
    encryption_key = sys.argv[5] if len(sys.argv) > 5 else "0123456789abcdef0123456789abcdef"

    settings = Settings(
        database_url=db_url,
        valkey_url=valkey_url,
        bot_token="123456:synthetic-token-for-test",  # noqa: S106
        cache_encryption_key=encryption_key,
        _env_file=None,
    )

    child = fork()
    if child == 0:
        try:
            init_worker_child(settings, namespace, UsageSafetyCaps())
            runner = get_worker_runner()
            outcome = runner.run(envelope_json, JobStage.MEDIA)
            shutdown_worker_child()
            if outcome not in {JobOutcome.COMPLETE.value, JobOutcome.FAILED.value}:
                _exit(2)
            _exit(0)
        except Exception:
            traceback.print_exc()
            _exit(1)

    _, status = waitpid(child, 0)
    assert status == 0, f"Child worker process failed with status {status}"
    sys.stdout.write(json.dumps({"pid": getpid(), "status": "ok"}) + "\n")


if __name__ == "__main__":
    main()
