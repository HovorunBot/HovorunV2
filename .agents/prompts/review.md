Review implementation of block `{{block_id}}` from:
`{{block_file}}`

Load your role skill `{{role_skill}}` (open the file directly if your tool lacks a skill system).
Read project constraints in `AGENTS.md` and required context files:
{{required_reads}}

Read implementation report at `{{report_path}}`.
Prerequisites: `{{depends}}`.

Audit actual code diff against requirements, architectural boundaries, and contracts. Verify that reported checks and test results are accurate and reproducible. Record findings under `{{initiative_path}}/reports/{{block_id}}-review.md` with severity and concrete file references.

State an independent review verdict (APPROVED / CHANGES_REQUESTED). Review approval does not authorize staging, committing, or deploying.
