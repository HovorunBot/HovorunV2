# Closing local initiatives

Completed implementation and closed initiatives have different meanings. `DONE` records verified block outcomes. Initiative `lifecycle: CLOSED` records that acceptance evidence and lasting standards have been reconciled and the plan has been archived. Missing lifecycle means `ACTIVE`; it never implies completion.

## Closure checklist

1. Validate the initiative with `make ai-check SCHEME=ai-schemes/<name>`. All blocks must be DONE with implementation reports. Review acceptance evidence, remaining findings, and limitations; all-DONE alone is insufficient. Reuse current verification evidence when repository state and scope match.
2. Create `ADOPTION.md` inside the initiative. Map every lasting contract to its tracked owner, enforcement mechanism, verification, and limitations. Put reusable engineering rules in `AGENTS.md`, documentation, skills/templates, test helpers, configuration, or tests. State explicitly when an initiative has no new lasting rules. Local plans are ignored and cannot be the only home of future standards.
3. Create `CLOSURE.md` recording closure date, acceptance report links, adoption review, unresolved limitations, follow-up references, and old/new locations. Unresolved required implementation or blocking findings prevent closure. Document accepted limitations without concealing them.
4. Set the manifest's initiative-level `lifecycle` to `CLOSED`. Keep block states and IDs unchanged. Validate again. Synchronize README, STATUS and the local index so no next-block instruction remains current.
5. Move the entire directory to `ai-schemes/archive/<name>/`. Check the destination first and refuse overwrite. Preserve all files. Rebase relative Markdown links from the moved documents and inbound links from active initiatives; record old snapshot paths as historical rather than rewriting their contents. Validate paths and planned edits before moving. If interrupted, inspect both locations and finish from the closure record; never delete or overwrite either directory to recover.
6. Validate the archived initiative explicitly and check live links. Remove it from the active index and list it in `ai-schemes/archive/README.md`. Default bulk validation skips the archive. Historical audits use `make ai-check SCHEME=ai-schemes/archive/<name>`; historical review prompts remain available explicitly.

Closure is a manual documentation workflow. It does not commit, push, deploy, migrate production, or erase history. Follow existing Git authorization rules. All local planning and archive files remain ignored.

## Future work

Read tracked project rules and active initiative context. Do not search or load archives by default. Read a specific archived contract only when the current task needs historical evidence. Archived implementation/planning prompts are rejected. Use a new linked active initiative for follow-ups; do not silently reopen or revise completed history.

## Durable standards

Clean-code constraints live in `AGENTS.md`, Ruff configuration in `pyproject.toml`, typing/import checks and repository hooks. Semantic decomposition, business-rule ownership and failure contracts require human or agent review; lint alone cannot prove them.

Tests follow architecture layers under `tests/`. Shared helpers belong in `tests/utils/`, never another test module. Reuse real Telegram models and the recording transport, worker-isolated PostgreSQL/Valkey fixtures, fresh-schema markers for DDL, and shared cache expiry. Mark pure tests `unit`; they must not request infrastructure fixtures. Preserve historical migration coverage and security/privacy regression assertions even when line coverage overlaps.

`make check` runs lint, typing, import boundaries and the full suite. `make test-unit` checks pure tests; `make test-fast` excludes marked heavy tests. Installed commit hooks run static checks but do not run pytest. Coverage comparison requires explicit evidence; there is no automatic non-decrease gate. New non-domain pure tests require deliberate unit marking. Pytest treats warnings as errors with an existing narrow socket-finalization exception. These limits must remain visible in adoption records.
