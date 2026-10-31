# Worker diagnostics

Set `ERROR_DM_ENABLED=True` in the private deployment environment to send worker errors to accounts with an active owner
role. Compose forwards this switch to application workers. Restart the workers after changing it. Owner lookup happens
for each alert, so revoked or deleted owners stop receiving alerts. No bot polling process is needed to deliver worker
alerts. Failed alert delivery writes a fixed stderr message instead of recursively logging another error.

Console progress logs remain available when owner alerts are disabled. Each feature execution includes its job ID, media
or browser stage, process name, and elapsed execution time. Parent receipt is logged before a child claims the task.
Look for these events in order:

1. `Worker parent received feature task`: the broker delivered the task to the worker parent.
2. `Worker received feature task` and `Worker claimed feature task`: a child began execution and acquired the claim.
3. `Starting ... extraction strategy`: identifies the active provider strategy. Browser continuation has separate
   request and publication events, followed by receipt on the browser worker.
4. Download and Telegram delivery events: distinguish extraction from fetching bytes and sending media. Telegram
   acknowledgement records how many messages were accepted.
5. `Worker finished feature task`: records the outcome and execution duration. Failure, expiry, unavailable payload,
   and uncertain delivery outcomes are error-level events. Access checks, duplicate claims, and quota waiting have
   their own diagnostic events; they do not imply that a media download started.

Inspect both execution queues together:

```bash
docker compose -f docker-compose.prod.yml logs --since=10m worker-media worker-browser
```

Missing parent receipt means the task has not been observed by that worker. Parent receipt without child receipt can
indicate busy children or child startup failure. A strategy-start event without completion narrows the stall to that
strategy. A browser-publication event without browser receipt narrows it to the continuation queue or browser worker.

Backend extraction shares a 30-second HTTP request budget. Instaloader uses a 30-second request timeout, one attempt,
and refuses rate-limit sleeps so another strategy can run. Celery publication includes a soft limit before the hard
execution limit, leaving up to ten seconds for cleanup. Extraction interrupted by the soft limit expires without usage
consumption. A timeout or failure after Telegram delivery starts retains conservative delivery reconciliation; it must
not cause an automatic resend. The parent also routes hard-timeout errors after the child is killed.

Errors include safe reason labels where available, such as authentication required, rate limiting, timeout, forbidden,
missing media, HTTP rejection status, or Telegram rejection. Backend diagnostics are classified heuristically; an unknown
reason remains `backend_unavailable`. Logs and alerts never include provider URLs, captions, credentials, or message bodies.
Actual delivery still depends on database access, reachable Telegram APIs, and a provisioned owner account.
