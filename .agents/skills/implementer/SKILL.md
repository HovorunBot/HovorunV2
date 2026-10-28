---
name: implementer
description: Use when executing an approved block from an ai-schemes initiative.
---

# Implementation Agent: Execute One Block

## Status Vocabulary

- `TODO`: Planned, not yet implemented.
- `IN_PROGRESS`: Actively being worked on by an agent.
- `BLOCKED`: Missing direct prerequisites or awaiting user clarification.
- `DONE`: Implementation complete with passing test evidence and a written report. (Does not require or imply a Git commit).

## Procedure

1. **Select block:** Work on the selected block. If explicitly asked to continue implementation without a block ID, select the first TODO block whose prerequisites are verified DONE in code and reports. Do not infer implementation authorization from a planning request.
2. **Review context:** Read the initiative README, execution rules, shared contracts, relevant decisions, chosen block, cited specification sections, and predecessor reports. Inspect actual source and workspace changes; keep context focused rather than loading every task file.
3. **Verify prerequisites:** Verify prerequisites and final predecessor contracts. Report missing dependencies or genuine business/preservation conflicts precisely. Never invent permissive placeholders or silently complete later blocks to compensate.
4. **Implement outcome:** Implement only the selected outcome, including applicable migration, transactional audit, deletion semantics, dependency injection, tests, and product/operator documentation. Preserve existing behavior where required and respect user changes and task exclusions.
5. **Run verification checks:** Run relevant checks and record actual results. Never equate schema creation with migration testing or claim skipped checks passed. If findings make the task exceed one coherent commit, split/update the plan before expanding scope.
6. **Write report:** Write a report using the initiative's template: behavior changed, files/contracts, migration, exact checks/results, limitations, resolved decisions, and proposed commit title. Update block/status references consistently using TODO, IN_PROGRESS, BLOCKED, or DONE. DONE requires implementation and acceptance evidence, not an actual Git commit.
7. **Preserve boundaries:** Keep all planning/status/review artifacts inside `ai-schemes/`. Application commits must contain only authorized implementation artifacts. Do not stage, commit, branch, push, tag, roll back, or deploy without explicit authorization; the manual-review requirement before committing still applies.
