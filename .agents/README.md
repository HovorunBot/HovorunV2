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

## DeepSeek Harness

Run `make deepseek` or `make ai TOOL=deepseek` to launch the official `npx @deepseek-ai/dsh web` command from the repository root. Node.js and npm/npx must be installed; npx may prompt to download the package on first use. The launcher does not change Harness permissions or approval settings.

Pass web arguments with `make deepseek ARGS="--port 3081 --no-open"`. Configure provider credentials and models privately through the Harness settings; the shared Makefile `MODEL` override is unsupported for this launcher. Do not place credentials in Make arguments. The repository's `AGENTS.md` rules and `.agents/skills/` procedures still apply to work performed through the Harness.

See the [official CLI instructions](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md) for installation and profile configuration.
