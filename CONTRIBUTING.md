# Contributing

Project conventions, privacy constraints and assistant instructions live in [AGENTS.md](AGENTS.md). Keep that file canonical instead of duplicating its rules. See [architecture](docs/architecture.md) for layer boundaries and application contracts.

## Development

Use Python 3.15+, uv and Docker for local PostgreSQL and Valkey. Install dependencies with `uv sync`. For the development bot container, supply `BOT_TOKEN` and `CACHE_ENCRYPTION_KEY` through your environment or private `.env`. `make run-dev` selects the complete `docker-compose.dev.yml` stack with public development database credentials. `make run` selects only `docker-compose.prod.yml`, requiring private server credentials. Use `APP_ENV_FILE=.env.dev` for separate development settings. `example.env` is the server template; `make env-init` generates server-only secrets without overwriting an existing file.

* `make run-dev`: Build and start the development bot, migration tool, PostgreSQL and Valkey in Docker.
* `make stop-dev`: Stop the development stack without deleting volumes.
* `make dev`: Start PostgreSQL and Valkey only, for host-side tests.
* `make compose-config COMPOSE_ENV=dev`: Validate the standalone development configuration.
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

Database tests require local PostgreSQL (`make dev`) or an explicit `TEST_DATABASE_URL`. See [PostgreSQL testing and transfer](docs/postgresql-migration.md).
