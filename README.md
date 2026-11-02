# HovorunV2

HovorunV2 is a Telegram group bot that expands media links, translates content and provides per-chat settings.
The project is in alpha.

## Features

- **Media previews:** Twitter (X), Instagram, TikTok, Reddit, YouTube, Threads, Bluesky and Facebook. Supported formats include videos, images, carousels and quoted posts, depending on the provider.
- **Translation:** Translate foreign-language content into the chat's target language, Ukrainian by default. Configure languages to leave unchanged.
- **Chat settings:** Use `/settings` to choose languages and toggle video or post/image capture independently for each provider.
- **Self-cleaning menus:** Configuration windows expire after 30 seconds of inactivity.
- **Release notifications:** Approved groups receive updates when new user-facing changes are deployed.

## Using the bot

Add the public instance to your group and use `/allow_chat` or the access button. Trusted users can activate it directly.
Other group administrators can request approval, which bot administrators review privately.
You can also host your own instance with Docker.

## Self-hosting

The supported runtime is Linux. On Windows or macOS, run the project in Linux containers; Windows developers can also use WSL2. Native Windows execution is unsupported.

Install Docker with Compose and `make`, then run:

```bash
make setup
```

Edit the generated `.env` file:

```dotenv
BOT_TOKEN=...
```

The generator creates database credentials and an encryption passphrase. Preserve an existing encryption key when upgrading. See [PostgreSQL setup](docs/postgresql.md) before starting an existing installation. Provision an owner using your numeric Telegram user ID; the example ID below is synthetic:

```bash
make migrate
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role add 123456789 --role owner"
make run
```

A dedicated migration container applies schema upgrades before the bot starts. Bot, media worker, operations worker and one Beat scheduler run as independent services and restart independently. Browser fallback is disabled by default. Set `BROWSER_ENABLED=True` in the selected environment file to build and start the separate browser worker with Chromium and DrissionPage. With `BROWSER_ENABLED=False` or an absent setting, Make builds only the lean image and stops any previously running browser worker on startup. Direct extraction remains available; requests requiring browser fallback fail promptly without queuing an unavailable worker or consuming quota. CLI administration needs PostgreSQL access and requires no Telegram or Valkey connection.

| Command | Purpose |
| --- | --- |
| `make run` | Start the bot, native workers, Beat, PostgreSQL and Valkey |
| `make stop` | Stop production services and preserve volumes |
| `make restart` | Restart services (optional `INVALIDATE=1` to clear transient runtime state) |
| `make run-dev` | Start the complete development stack in Docker |
| `make stop-dev` | Stop development services and preserve volumes |
| `make dev` | Start development PostgreSQL/Valkey for host-side tests |
| `make update` | Pull changes and rebuild |
| `make migrate` | Apply database migrations in Docker |
| `make clean` | Remove unused application images, stopped stack containers, build caches and local artifacts; preserve volumes |

Each environment has one complete Compose file: `docker-compose.dev.yml` or `docker-compose.prod.yml`. Make selects production by default; `COMPOSE_ENV=dev` selects development for any Docker target. There is no base file or overlay. Use `APP_ENV_FILE=.env.dev` when keeping separate local/server settings.

Builds require Docker Buildx. They use a temporary builder with `--no-cache`, cleaning up build cache upon completion. The multi-stage runtime image excludes compilers, apt download lists and uv caches. Previous application images stay available during builds and startup checks. Docker logs rotate at three 10 MB files per container.

Run `make clean` to reclaim artifacts from older deployments, or `make COMPOSE_ENV=dev clean` locally. It also clears the selected Docker builder's unused cache. It does not stop running application containers or remove PostgreSQL, Valkey, Beat volumes, `.env`, `data/` or `.venv/`.

Default resource caps, memory ceilings, and operational settings can be customized via the variables documented in `example.env`. Base images and tools are pinned by digest; builds remain local and target the host architecture.

Use Make to apply the `.env` browser switch. Raw `docker compose` commands require `--profile browser` when enabling the browser service; explicitly naming a profiled service bypasses profile selection.

See [administration](docs/administration.md) for role and trust commands, access rules and troubleshooting.

## Access and privacy

Bot authority, trust and group approval are separate. Only CLI operations can change bot-administrator authority or their personal restrictions.
Group bans and full bans on current Telegram administrators suspend group processing for everyone. Group processing also requires fresh administrator evidence.

The bot retains the identity and operational data needed for access control and its features. Temporary message cache entries are encrypted per chat and exclude contacts, locations, user profiles, forwarding metadata and nested replies.
Operational logs omit message URLs, exception contents and credentials while retaining operation names, exception types and code locations for debugging.

## Development

Built with Python 3.15+, aiogram, SQLAlchemy/PostgreSQL, Valkey, Celery and Dishka; DrissionPage is installed only in the browser-worker image target.
See the [contributor guide](CONTRIBUTING.md) for setup, checks and contribution conventions, and [architecture](docs/architecture.md) for implementation details.

## License

[BSD 3-Clause](LICENSE).
