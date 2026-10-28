# PostgreSQL mutation discipline

Every business mutation and its audit event commit in one database transaction. All application writers use the following discipline; repositories never acquire a global writer lock. No transaction stays open across provider HTTP, Telegram delivery or browser work.

`MutationLocks` declares the strongest lock required for every subject before loading mutable facts. `lock_mutation` resolves immutable parent references, acquires sorted natural-key gates and then user rows followed by chat rows in primary-key order. Target subjects use exclusive locks; actor-only facts use compatible shared locks. An actor who is also a mutation target receives the exclusive mode immediately. Current authority, restrictions and generation are re-read after locks. Repositories operate on that transaction's session.

Scoped transaction advisory gates protect missing identities and keys that have no parent row: concurrent ingestion, new routes, handle names, system setting keys and fixture registration. Existing subjects also receive PostgreSQL row locks. Gates are stable hashes of schema plus operational key, never Python's randomized hash. They are not an application-wide writer lock; unrelated subjects proceed concurrently. Gates use SQLAlchemy function expressions rather than raw SQL.

| Writer | Exclusive parent/key | Shared facts | Race protection |
| --- | --- | --- | --- |
| User ingestion, trust, deletion and CLI role provisioning | Numeric user identity, including tombstones and absent identity gate | Telegram trust operator, when applicable | One active identity, no resurrection; trust checks current role inside transaction |
| Chat ingestion, settings, presence/status, encryption salt and deletion | Numeric chat identity and resolved alias parent | Authenticated command-selection actor | Settings/selection cannot race deletion, defaults or another edit |
| Group routing migration | Both old/new numeric routes and source/target parents | None | Reject collisions with historical/deleted identities and preserve associations |
| Bans | Target user/group identity | Telegram operator | Restriction uniqueness and protected-role checks inside transaction |
| Activation | Group parent | Requesting/operator user | Recheck trust/role, bans, generation and request binding before approval/defaults |
| Group grants and access requests | Group parent resolved from request when necessary | Requester and actor | One pending request; decisions and approval commit together |
| Beta entitlements and usage overrides | Exact subject parent | Operator | Read current authority/generation before insert/revoke; partial uniqueness remains authoritative |
| Group beta selection/support | Group parent | Operator/enabler | Membership, grant, selection and support changes cannot bypass their parent lock |
| Membership verification | Group parent and users that may be ingested | None | Newer evidence only; current parent/generation remains authoritative |
| Public handles | Numeric target plus normalized handle name | None | Target and handle unique constraints remain authoritative; conflicting assignments retry only after transaction rollback |
| System settings | Exact setting key | None | Protect absent key and read/update/insert race |
| Fixture seeding | Command fixture namespace | None | ON CONFLICT DO NOTHING; audit only actually inserted definitions |
| Usage accounting | Receipt event ID and aggregate unique key, acquired by PostgreSQL conflict handling | Known current/historical identity lookup | ON CONFLICT receipt claim, fingerprint comparison, atomic guarded aggregate increment; receipt/counters/audit roll back together |

Accounting is historical and does not grant execution authority. Soft-deleted generations remain eligible for delayed settlement. It does not require exclusive user/group locks to add unrelated accounting events. Overflow guards reject the entire batch before a bigint addition can overflow. Replay with a different fingerprint aborts rather than silently accepting different charges.

Deadlock/serialization failures (`40P01`, `40001`) retry the entire database-only audited callback up to three attempts using fresh sessions. Other errors propagate without retry. This bounds the remaining cross-key conflicts such as concurrent route/handle changes; it does not repeat external effects or grant a new execution deadline. Uniqueness/audit-validation failures are not silently retried or ignored.

Migration/import exclusion is separate from normal mutations. A production transfer still requires stopping old writers and using an empty target. Independent subject concurrency is verified with a barrier test that holds both transactions before either commits, including a shared actor. Eventual success after serialized execution would not satisfy that check.
