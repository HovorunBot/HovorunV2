# Usage policies and accounting

Usage policy resolution distinguishes the charged subject from the Telegram transport.
Direct feature commands charge the active user, including commands sent in a group.
Automatic group work charges the active group. Group defaults do not depend on the
trust or role of the sender or activating user. Access and feature availability remain separate checks.

Each media feature resolves both its `feature:<key>` bucket and the shared `family:media`
bucket. Both apply, so switching providers cannot evade the shared limit.
Policies use typed units and positive fixed UTC windows; additional configured windows apply together.
An exact subject/bucket/unit/window override takes precedence over the matching default.
Trusted-user defaults apply only to personal usage. Unsupported resource policies fail closed.

Initial media defaults, pending release load validation:

| Subject | Requests/minute | Burst | Concurrent jobs | Pending jobs |
| --- | --- | --- | --- | --- |
| Ordinary user | 6 | 3 | 1 | 10 |
| Trusted user | 12 | 6 | 2 | 10 |
| Automatic group | 12 | 6 | 2 | 25 |

Bot administrators bypass ordinary personal quotas. Automatic group quotas and all
application safety caps still apply. Ordinary overrides cannot change these separate ceilings:
two global concurrent jobs, 500 global pending jobs, 64 KiB encrypted payload per request,
32 MiB total pending payload, five-minute acceptance deadline and at most 120 seconds
per execution, further bounded by the remaining deadline.

Override writes require actual CLI origin or current unrestricted bot administration in
private transport. Mutations and before/after audit share one transaction. Removal is soft;
deleted subject generations and deleted overrides do not appear in ordinary policy reads.
Zero and negative quota/window values are invalid, and burst cannot exceed the window limit.
No override stores application safety caps, private messages, resource prompts or provider credentials.

The current policy foundation defines admission inputs; live reservation, queue admission,
workers, reconciliation, and user-facing limit controls are implemented in later blocks.
No resource provider, AI product, billing or paid access tier is introduced.

## Durable accounting

`UsageLedgerStore` records successful measured consumption in bounded batches. Each technical
completion or resource checkpoint has a global event ID and a fingerprint of its accounting fields.
Replaying the same event has no effect, even when charge order changes. Reusing its ID for
different subject, timestamp or charges rejects and rolls back the entire batch.

Aggregates use platform, user/group kind, stable numeric Telegram identity, feature/family bucket,
unit, window duration and UTC window start. Internal entity IDs and generations are excluded,
so deleting and recreating a subject cannot reset the current-period budget. Known historical
identities may receive delayed accounting without being recreated or authorized to execute work.
Accounting aggregates and replay receipts are retained independently of soft-deleted configuration.
Unknown subjects cannot be provisioned through accounting.

A single serialized SQLite transaction commits replay receipts, aggregate increments and one
payload-free audit summary. Failed audit insertion rolls back all three; retries can safely use
the same event ID. Resource amounts are aggregated at completion/checkpoint boundaries, without
one receipt or audit per token. Summary `units_added` counts aggregate increments across dimensions;
feature and family counters each receive the relevant consumption.

Batches contain at most 256 events, each with at most 256 distinct bucket/unit/window charges.
Amounts and timestamps use bounded integers; overflow fails instead of losing integer precision.
Only trusted system/CLI accounting paths may mutate these records. They store operational
counter dimensions, technical replay IDs, fingerprints and timestamps, without request contents,
URLs, prompts, message data, provider credentials or executable jobs.

Apply additive Alembic revisions before running code that uses these stores: `limits036`
creates policy overrides and `usage037` creates accounting tables. No accounting history is
backfilled from messages. Downgrading `usage037` discards its accounting tables and history.
Runtime Valkey reservations and SQLite accounting do not share a transaction. Live admission,
worker integration, crash/restart reconciliation and checkpoint scheduling remain later work;
this foundation makes no cross-store atomicity or exactly-once delivery claim.
