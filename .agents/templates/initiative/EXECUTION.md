# Execution Rules — <Initiative Title>

Target implementers: Follow literally.

1. **One Block per Session:** Read in order: `AGENTS.md`, this file, `CONTRACTS.md`, `DECISIONS.md`, the chosen block, and cited predecessor reports. `ai-schemes/` is git-ignored; open paths directly.
2. **Verify Prerequisites:** Every direct prerequisite in `Depends:` must be verified `DONE` in `STATUS.md` and have an implementation report under `reports/`. Stop if blocked.
3. **No Unresolved Decisions:** If a block touches an `OPEN` decision, stop and ask the user for clarification before modifying code.
4. **Focused Scope:** Implement only the selected block. Do not build forward into subsequent blocks or introduce permissive placeholders.
5. **Real Infrastructure Testing:** Run tests against real PostgreSQL/Valkey as configured in project conventions. Never mock internal persistence stores.
6. **Report and Status Sync:** Run verification checks, write `reports/<ID>.md` using `REPORT_TEMPLATE.md`, and update the block header, `STATUS.md`, and `manifest.json` together.
7. **No Unauthorized Git Actions:** No staging, committing, branching, or pushing without explicit instruction. Provide a suggested Conventional Commit title in the report.
