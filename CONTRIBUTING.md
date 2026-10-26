# Contributing

Project conventions, privacy constraints and assistant instructions live in [AGENTS.md](AGENTS.md). Keep that file canonical instead of duplicating its rules. See [architecture](docs/architecture.md) for layer boundaries and application contracts.

## Development

Use Python 3.15+, uv and Docker for local Valkey. Install dependencies with `uv sync`, then copy `.env.example` to `.env` and configure the required credentials before running the bot.

* `make run-dev`: Local bot execution with Valkey in Docker.
* `uv run ty check src`: Strict type verification.
* `uv run ruff check .`: Linting.
* `uv run ruff format --check .`: Formatting verification.
* `uv run lint-imports`: Enforce onion architecture layer contracts.
* `PYTHONPATH=src uv run pytest`: Run unit and integration test suite.
* `make install-hooks`: Install quality, Conventional Commit, and post-commit changelog hooks.

After each commit, the post-commit hook generates `CHANGELOG.md` with Commitizen and amends
that file into the same commit if needed. The amendment preserves the message, author, and
unrelated staged changes; hooks are disabled only for that generated-file amendment to avoid
recursion. Install hooks in each checkout before committing.

The hook refuses to overwrite local changelog edits or amend a tagged commit. A post-commit
failure leaves the original commit in place: resolve the reported issue, run `make changelog`,
review the generated changes, and amend the commit before tagging or pushing.

## AI-assisted contributions

AI tools are allowed. The commit author remains responsible for reviewing, understanding and testing the submitted code. Follow the same architecture, privacy and quality requirements for generated code.
