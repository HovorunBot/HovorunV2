# Assistant Roles Guide

Project-local role definitions for `ai-schemes/` initiatives.

## Role Responsibilities and Boundaries

| Role | Skill | Responsibilities | Reads | Writes | Suggested Tier |
|---|---|---|---|---|---|
| **Architect** | `.agents/skills/architect/SKILL.md` | Analyze repository, draft specifications, decompose tasks into acyclic commit-sized blocks, define contracts, and maintain plan status/manifest. | Codebase, existing schemes, docs, tests | `ai-schemes/<initiative>/{SPECIFICATION,CONTRACTS,DECISIONS,COVERAGE,STATUS,manifest.json,blocks/*}` | Strong tier (e.g. Gemini 4 Argon, Claude Opus 5.5, GPT-6.1 Sol) |
| **Implementer** | `.agents/skills/implementer/SKILL.md` | Execute one approved block at a time. Implement code changes, migrations, DI wiring, and tests. Run verification checks and produce implementation report. | Selected block, direct prerequisites, contracts, reports, source code | Source code (`src/`, `tests/`), implementation report (`ai-schemes/.../reports/<ID>.md`), block status | Fast or Strong tier (e.g. Gemini 3.8 Flash, Claude Sonnet 5.5, Codex / GPT-6.1) |
| **Reviewer** | `.agents/skills/reviewer/SKILL.md` | Independently audit implemented block diff against requirements, architecture boundaries, and test evidence. Verify acceptance criteria. | Block diff, tests, reports, contracts, source code | Review reports (`ai-schemes/.../reports/<ID>-review.md`), finding remediation routes | Strong tier, ideally a different model or tool from the implementer |

## Parallel Execution Policy (STRICT)

- Work is sequential by default in a single workspace.
- Subagent delegation, parallel execution, and isolated worktrees/branches are permitted **only when the user explicitly requests parallel work in that session** (see `AGENTS.md` §1 Hard Constraints).
- When authorized for parallel execution, workers rebase results onto the current branch; never merge, and never commit or push without explicit user approval.
