---
name: run-workflow
description: Coordinate staged or explicitly authorized parallel ai-schemes work through the run_workflow tool, preserving architect, implementer, and reviewer procedures. Use for multi-role orchestration, not a single block or ordinary tool batching.
---

# Coordinate initiative workflows

Use this skill to orchestrate the repository's existing roles. It does not replace their procedures or authorize implementation, delegation, Git operations, or deployment.

## Availability and authorization

- Confirm that the environment exposes `run_workflow` before writing a script. Read [the runtime guide](references/runtime-guide.md) for its API. A skill file does not install the tool.
- If the tool is unavailable, carry out authorized work directly using the matching role skill and report the limitation. Do not invent an equivalent API or install a runner without instruction.
- Follow [the parallel execution policy](../../roles.md). Delegation, parallel agents, and isolated workspaces require explicit user authorization in the current session. Otherwise work directly and sequentially.
- Session-specific restrictions still apply, including environments that forbid agents or worktrees. Tool availability never overrides them.

## Role handoffs

1. Inspect `AGENTS.md`, the active initiative's specification, contracts, decisions, execution rules, manifest, status, and relevant reports. Discover active initiatives only. A planning request authorizes planning alone.
2. Use [architect](../architect/SKILL.md) for specifications, block boundaries, and dependencies. Use [implementer](../implementer/SKILL.md) for each authorized block. Use [reviewer](../reviewer/SKILL.md) for independent validation of its actual diff and evidence.
3. Select eligible blocks with `make ai-next SCHEME=ai-schemes/<initiative>`. Verify direct prerequisites in source and reports; status labels alone are insufficient. For several initiatives, check their documented cross-initiative prerequisites and shared ownership as well. Do not run dependent blocks concurrently.
4. Give each authorized agent explicit scope, relevant paths, required role skill, allowed writes, acceptance checks, and output schema. Include the applicable repository instructions and authorization limits in its prompt; do not assume agents share conversation context.
5. Use named phases for planning, implementation, and review when those stages are authorized. Keep each implementer to one coherent block. Route review findings back to that block; review approval and implementation DONE remain separate facts.
6. Inspect reports and actual results before synchronizing status. DONE requires implementation and passing acceptance evidence, not a successful workflow return. A failed or cancelled workflow may leave partial edits: inspect them without discarding user work or claiming rollback.

## Parallel ownership

When parallel work is explicitly authorized, assign disjoint file ownership before starting writers. Serialize edits to shared contracts, manifests, status files, dependency locks, composition roots, and other overlapping files. Keep one coordinator responsible for reconciling initiative state after checking results.

Use isolated workspaces only within the user's authorization. Workflow workspace modes do not grant permission to branch, commit, merge, or rebase. Integrate changes only through separately authorized repository operations; otherwise leave concrete results for review.

## Artifacts and verification

- Store orchestration scripts and experiment artifacts under `ai-schemes/<initiative>/experiments/`; keep implementation and review reports under its `reports/`. All remain ignored and must never be staged.
- Pass only necessary context to agents. Never include `.env` values, credentials, Telegram message contents, or payload-bearing diagnostics in prompts, schemas, logs, or reports.
- Run each block's meaningful checks using the project's real-infrastructure and warning rules. Record failed, blocked, and skipped checks accurately.
- Validate synchronized plans with `make ai-check SCHEME=ai-schemes/<initiative>`. Follow `docs/initiative-closure.md` only after verified implementation; a workflow finishing does not close an initiative.
