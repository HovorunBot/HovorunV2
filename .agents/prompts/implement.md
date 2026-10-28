Implement block `{{block_id}}` from:
`{{block_file}}`

Load your role skill `{{role_skill}}` (open the file directly if your tool lacks a skill system).
Read project constraints in `AGENTS.md` and required context files:
{{required_reads}}

Note that `ai-schemes/` is git-ignored: open these paths explicitly if search omits them.

Prerequisites: `{{depends}}`.
Verify all direct prerequisites are marked DONE in `{{initiative_path}}/STATUS.md` and have corresponding implementation reports. If any prerequisite is missing or an OPEN decision blocks implementation, report BLOCKED and stop.

Implement only `{{block_id}}` as one coherent reviewable commit-sized change. Preserve surrounding behavior and user changes. Run required verification checks, write implementation report to `{{report_path}}`, and update `{{block_file}}`, `{{initiative_path}}/STATUS.md`, and `{{initiative_path}}/manifest.json` status together.

No Git staging, commit, branch, push, tag, or rollback is authorized. Suggest a Conventional Commit title in your report.
