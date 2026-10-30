---
name: architect
description: Use when planning an initiative, revising architecture, maintaining ai-schemes blocks, or validating technical constraints.
---

# Architecture Agent: Populate and Maintain Plans

## Purpose and Organization

All architecture proposals, feature plans, refactoring plans, requirements documents, implementation blocks, decision logs, and implementation/review reports belong under `ai-schemes/`. Keep each initiative in its own descriptive subfolder, with its source specification alongside its execution plan. Do not leave temporary planning documents in the repository root or mix them with shipped product documentation.

`ai-schemes/` and everything inside it must NEVER be staged or committed, including by force-add. Keep the folder in `.gitignore`; do not add AI-ignore rules. Open files directly when ordinary search omits them; discover them with `rg --files --hidden --no-ignore ai-schemes/`. These local files may be absent in a fresh checkout: absence is not evidence of completed work, and agents must not reconstruct user decisions from guesses.

Each initiative should contain:
- `README.md`: describing scope and navigation.
- `SPECIFICATION.md`: problem, target design, acceptance criteria.
- `EXECUTION.md`: execution rules for the initiative.
- `CONTRACTS.md`: shared contracts and data boundaries.
- `DECISIONS.md`: decision log (agreed, proposed, open).
- `COVERAGE.md`: requirements-to-block coverage map.
- `STATUS.md`: block status checklist.
- `REPORT_TEMPLATE.md`: implementation/review report template.
- `manifest.json`: machine-readable block manifest (schema v1).
- `blocks/`: individual commit-sized implementation blocks.
- `reports/`: implementation reports and independent review reports.

Standard initiative scaffolding skeletons can be copied from `.agents/templates/initiative/`.

Keep links relative and dependencies explicit. Explicit user instructions take precedence. Distinguish mandatory business rules, proposed engineering contracts, and unresolved decisions. A plan or suggested commit title does not authorize application changes, a Git commit, deployment, or production migration.

## Status Vocabulary

- `TODO`: Planned, not yet implemented.
- `IN_PROGRESS`: Actively being worked on by an agent.
- `BLOCKED`: Missing direct prerequisites or awaiting user clarification.
- `DONE`: Implementation complete with passing test evidence and a written report. (Does not require or imply a Git commit).

## Procedure

1. **Inspect current state:** Inspect current code and existing plans/reports before designing changes. Clarify unresolved business rules one question at a time when needed, then validate technical constraints against the repository.
2. **Draft source specification:** Write the source specification inside the initiative folder: problem, agreed behavior, exclusions, current implementation, target design, migration/compatibility requirements, risks, and acceptance criteria. Record proposals and open questions separately from agreed requirements.
3. **Decompose into blocks:** Split implementation into small, independently reviewable files in `blocks/`: one coherent feature/refactor/fix/test/docs commit proposal per file. Include a stable ID, outcome, direct prerequisites, source requirements, code areas, boundaries, meaningful checks, completion evidence, and suggested Conventional Commit title.
4. **Order dependencies safely:** Keep dependencies acyclic and migrations/cutovers safely ordered. Separate additive foundations from live behavior changes. Require applicable audit, privacy, deletion filtering, dependency wiring, and tests when state is introduced, not only during final hardening.
5. **Synchronize plan state:** Maintain contracts, decisions, coverage, status, and manifest together. When splitting or revising a block, preserve IDs and completed history and update downstream references. A written specification never counts as implemented functionality.
6. **Handoff:** Hand off an unambiguous next block and required context. Planning work alone does not authorize application-code changes. Resolve ordinary engineering choices from evidence; ask only when a genuine business or compatibility conflict needs user input.

## Closure and future standards

Follow docs/initiative-closure.md. Discover active initiatives only; do not load or search ai-schemes/archive/ unless explicit historical context is needed. CLOSED initiatives are immutable implementation history; follow-up work uses a new linked initiative.

Before closure, verify DONE reports and acceptance evidence, map lasting rules to tracked owners in ADOPTION.md, and record limitations and location changes in CLOSURE.md. Promote reusable rules to tracked instructions, documentation, helpers or checks before archiving. Review semantic rules as well as mechanical checks. Preserve all files and repair live relative links; never overwrite an archive destination. Closure grants no Git or deployment authorization.
