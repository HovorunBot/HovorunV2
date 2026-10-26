# Telegram group migration

[Administration](administration.md) · [Architecture](architecture.md)

Telegram's basic-group to supergroup service messages update the existing group's delivery address. The internal group identity, approvals, pending requests, feature selections, language settings, encryption salt and audit references remain attached to the same record. Group bans follow the new address. Membership and bot-capability evidence must be refreshed before processing resumes.

The old address remains a historical routing alias. Application callers can resolve it through `GroupMigrationStore.canonical_id`; it never provisions a second group. Messages, callbacks and membership events using retired routing cannot rewrite current public handles or perform group actions. Private approval buttons issued before migration must be refreshed using `/allow_chat` in the current group.

Repeated migration events are harmless. Conflicts with an existing or deleted target identity or another handle mapping leave both identities untouched. There is no administrator command to merge unrelated groups. Conflicting or unknown-source migrations require operator investigation; service messages never fabricate approvals or identities.

Apply Alembic migrations before starting the upgraded bot. The `group025_routing_aliases` revision adds routing history without rewriting existing group records. Routing, ban and handle changes commit with one minimal audit event; a failure rolls back the whole migration. Historical audit events retain the address recorded at the time.

This routing foundation does not move existing Valkey message keys or queued jobs. Later delivery code must resolve the current address explicitly. Existing encryption salts remain unchanged, so retained encrypted payloads keep their original key material.
