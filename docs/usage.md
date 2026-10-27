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
