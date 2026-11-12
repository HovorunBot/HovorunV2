# `.agents` — Project-Local Assistant Customizations

Tracked workspace customization directory providing tool-neutral skills, role guides, prompts, and initiative templates.

## Directory Layout

- `skills/`: Standard on-demand skills with YAML frontmatter:
  - `skills/architect/SKILL.md`: Planning, specification drafting, and block decomposition.
  - `skills/implementer/SKILL.md`: Single-block implementation procedure and verification.
  - `skills/reviewer/SKILL.md`: Independent verification, acceptance criteria audit, and review reporting.
  - `skills/run-workflow/SKILL.md`: Optional orchestration of the existing roles when the environment exposes `run_workflow`; delegation and parallel execution require explicit authorization.
- `roles.md`: Role responsibilities, boundaries, model tier guidelines, and parallel execution constraints.
- `prompts/`: Role prompt templates with standard placeholders.
- `templates/`: Skeletons for scaffolding new `ai-schemes/` initiatives.

## How Assistant Tools Load This Repository

| Assistant Tool | Instruction Entry Point | Skill Discovery Mechanism |
|---|---|---|
| **Antigravity (`agy`, alias `gemini`)** | `GEMINI.md` (symlink -> `AGENTS.md`) | Native progressive disclosure from `.agents/skills/<name>/SKILL.md` |
| **OpenAI Codex** | `AGENTS.md` (root instructions) | Opens `.agents/skills/<name>/SKILL.md` directly as referenced in `AGENTS.md` |
| **Claude Code** | `CLAUDE.md` (`@AGENTS.md` import) | `.claude/skills/<name>` (relative symlinks to `.agents/skills/<name>`) |
| **Custom Runner / Harness** | `AGENTS.md` (root instructions) | Loads `.agents/skills/<name>/SKILL.md`; orchestrates plans via `scripts/ai_scheme.py` |

Launch assistant tools directly through their own interfaces. The repository retains skills, prompts, templates and the initiative CLI without wrapping assistant executables.
