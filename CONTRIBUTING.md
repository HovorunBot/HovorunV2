# Contributing

Project conventions, privacy constraints and assistant instructions live in [AGENTS.md](AGENTS.md). Keep that file canonical instead of duplicating its rules. See [architecture](docs/architecture.md) for layer boundaries and application contracts.

Contributors and LLM assistants must follow the [modern Python requirements](AGENTS.md#modern-python-requirements-for-contributors-and-llms), including selective native lazy imports, runtime annotation compatibility and the intentional Ruff `TC` exclusion.

## Development

Use Linux, WSL2, or a Linux container for development and worker execution. Native Windows execution is unsupported; native macOS is not a supported worker runtime.

Browser discovery checks an executable `BROWSER_PATH` override first, then package installations, Flatpak, Snap, and AppImage, in that order, even when PATH lists Snap first. Package locations include common Unix binary directories, Homebrew prefixes, and known Chromium browser bundles in `/Applications` and `~/Applications` on macOS.

Flatpak discovery includes user and system exports. Browser AppImages are searched non-recursively in PATH directories, `~/Applications`, `~/AppImages`, and `/opt/appimages`; downloads must be executable. This discovery support does not change the supported worker runtime. For a browser installed elsewhere, set `BROWSER_PATH` explicitly.

Use Python 3.15+, uv and Docker for local PostgreSQL and Valkey. Install dependencies with `uv sync`; `DrissionPage` remains a development dependency for browser tests and is installed in the browser-worker image through the `browser` extra. For the development containers, supply `BOT_TOKEN` and `CACHE_ENCRYPTION_KEY` through your environment or private `.env`. `make run-dev` selects the complete `docker-compose.dev.yml` stack with public development database credentials. `make run` selects only `docker-compose.prod.yml`, requiring private server credentials. Use `APP_ENV_FILE=.env.dev` for separate development settings. `example.env` is the server template; `make env-init` generates server-only secrets without overwriting an existing file.

* `make run-dev`: Build and start the development bot, native workers, Beat, migration tool, PostgreSQL and Valkey in Docker.
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

## Releases

Commit pending work before running `make tag-release VERSION=vX.Y.Z`, or use `INCREMENT=patch`, `minor`, or `major`.
Without either option, Commitizen derives the increment from Conventional Commits. `release-tag` is an alias;
`TAG=vX.Y.Z` remains supported. `MESSAGE="Release notes"` supplies the annotated tag message as literal text.

The command requires a clean workspace and index. Commitizen's `uv` version provider updates the project version in
`pyproject.toml` and only the matching project package in `uv.lock`. It generates `CHANGELOG.md` for the target release,
commits all three files together, then creates an annotated tag pointing at that commit. Unrelated dependency versions
remain unchanged. The ordinary changelog amendment hook is skipped during this operation because the release changelog
is already generated and the tag does not exist yet. Normal commits retain the usual changelog hook behavior.

No release command pushes commits or tags automatically. Review the release commit and tag before pushing them.

## AI-assisted contributions

AI tools are allowed. The commit author remains responsible for reviewing, understanding and testing the submitted code. Follow the same architecture, privacy and quality requirements for generated code.

See [AGENTS.md](AGENTS.md) for canonical instructions, privacy constraints, and coding conventions. See [.agents/](.agents/README.md) for tool-specific setup (Antigravity, Codex, Claude Code), role guides, and initiative templates. Assistants can be launched with `make ai TOOL=<name>` or dedicated shortcuts `make agy`, `make codex`, `make claude`. Plan validation and progression are available via `make ai-check` and `make ai-next`.

Database tests require local PostgreSQL (`make dev`) or an explicit `TEST_DATABASE_URL`. See [PostgreSQL testing](docs/postgresql.md).
