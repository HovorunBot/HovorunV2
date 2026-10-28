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

A single PostgreSQL transaction commits replay receipts, aggregate increments and one
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
Runtime Valkey reservations and PostgreSQL accounting do not share a transaction. Live admission,
worker integration, crash/restart reconciliation and checkpoint scheduling remain later work;
this foundation makes no cross-store atomicity or exactly-once delivery claim.

## Execution reservations

BudgetService uses an injected clock and durable usage totals to seed missing runtime windows.
ValkeyBudgetStore atomically checks every fixed-window quota, token-bucket burst allowance and
concurrency set before acquiring any capacity. Subject keys use stable numeric identity and
feature/family/unit/window dimensions. Global concurrency remains independent of ordinary quota
bypass, and group quota cannot be bypassed by an administrator sender.

Reservations have technical IDs and immutable operation fingerprints. An identical retry returns
the existing state without another debit; conflicting reuse fails. Settlement commits actual
consumption up to its estimate, refunds unused units, and releases concurrency exactly once.
Zero actual consumption releases the reservation. Resource estimates must be increased through
a future explicit reservation before consumption exceeds the reserved amount.

Waiting returns a next retry time immediately; concurrency hints ask the scheduler to recheck
rather than guarantee availability. No worker sleeps here. Valkey errors or missing state pause work.
Active reservations retain their capacity after deadline until explicit settlement/recovery;
expiry must not silently permit overlapping execution. Terminal receipts remain for one hour,
longer than request deadlines. Orphan recovery belongs to the terminal/reconciliation blocks.
PostgreSQL accounting and runtime settlement remain separate replayable operations.

## Feature job envelopes and encrypted payloads

FeatureQueuePort carries a strict JobEnvelope containing technical job/payload references,
feature, canonical target/generation, actor/generation, charging scope, original acceptance/deadline,
hashed deduplication identity, feature epoch and delivery message ID. Payload contents, URLs,
Telegram objects, arbitrary kwargs and profile fields are excluded. Automatic work binds the
charged group to its target; direct work binds the charged user to its actor.

ValkeyPayloadStore prepares independently salted Fernet ciphertext without changing existing
per-chat derivation. Actual stored ciphertext bytes, including encryption overhead, count against
the request bound. Payload retention covers the remaining original deadline plus a 60-second
cleanup margin. Rewriting the same payload never extends retention. Missing, expired, unavailable
or undecryptable input produces a safe typed outcome with no plaintext fallback.

SAQFeatureQueue owns a separate queue from operational tasks. Enqueue/schedule/inspect/cancel
use typed application contracts; SAQ jobs stay in infrastructure. Job timeout bounds execution,
not the acceptance deadline. Job records retain one hour after completion. FeatureJobRunner and
FeatureWorkerBridge convert executor exceptions and results into fixed outcome codes; serialization
also strips arbitrary metadata and sanitizes result/error fields before persistence and finish logs.
The feature queue uses SET with expiry for terminal persistence. No feature worker starts in this
foundation; worker cutover must also address the stock SAQ 0.26.4 deprecated coroutine probe.

## Bounded admission and queue handoff

AdmissionService checks current actor/target generations, canonical routing, feature access and
resolved policy before accepting work. Deduplication hashes stable incoming action metadata
(platform, target, actor, message, trigger and feature), never a URL or message body.
ValkeyAdmissionStore atomically checks subject pending count, per-sender ingress count, global
pending count, per-payload ciphertext size and total ciphertext bytes. Only then does it store
ciphertext, reserve counters and create an accepted ticket. Overflow leaves accepted work untouched.
Duplicates return their original envelope and do not reserve capacity or store another payload.

Initial ingress cap is ten pending jobs per sender in a group, further bounded by subject policy.
Counts share stable subjects across features. Personal administrator quota bypass does not remove
pending/ingress/global/byte safety bounds. Actual ciphertext blob bytes are counted once; tickets
contain no duplicate ciphertext. Original envelope JSON remains verbatim inside the ticket so Lua
JSON number formatting cannot change deadlines, large numeric identities or SAQ replay checks.

Acceptance and SAQ enqueue are two phases. Accepted tickets appear in a bounded recovery index.
A retry enqueues by the original stable job ID, accepts an existing identical SAQ job, then marks
the ticket enqueued. Failure before enqueue or after enqueue/before acknowledgement leaves a
recoverable accepted ticket with capacity held. No cross-system transaction is promised.

Only verified terminal completion/cancellation may release counters and remove ciphertext.
Terminal release is idempotent; changed terminal outcomes fail. Empty subject/ingress counters
are removed. Active tickets/dedupe/capacity do not silently expire while work might still run.
Expired accepted work retains capacity for the separate terminal sweeper; recovery skips it rather
than blocking live tickets. Terminal ticket/dedupe receipts retain one hour. Callers must preserve
original acceptance/deadline on retry. Fair scheduling, live media cutover, terminal sweeping and
restart reconciliation remain later implementation steps.
