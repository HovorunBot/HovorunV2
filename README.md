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
make compose-run ARGS="--no-deps bot hovorun-admin role add 123456789 --role owner"
make run
```

A dedicated migration container applies schema upgrades before the bot starts. Bot, media worker, operations worker and one Beat scheduler run as independent services from dedicated, role-optimized container images and restart independently. Browser fallback is disabled by default. Set `BROWSER_ENABLED=True` in the selected environment file to build and start the separate browser worker with Chromium and pydoll-python. With `BROWSER_ENABLED=False` or an absent setting, Make builds the four standard role images (`hovorunv2-migrate`, `hovorunv2-bot`, `hovorunv2-worker-ops`, `hovorunv2-worker-media`) and stops any previously running browser worker on startup. Direct extraction remains available; requests requiring browser fallback fail promptly without queuing an unavailable worker or consuming quota. CLI administration needs PostgreSQL access and requires no Telegram or Valkey connection.

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

Builds require Docker Buildx with BuildKit enabled (`DOCKER_BUILDKIT=1`). Builds leverage persistent daemon BuildKit layer caching and package caches across invocations. Passing `FORCE=1` rebuilds images without cache. The multi-stage runtime images exclude compilers, build tools, package managers, and duplicate source files, executing directly via `/app/.venv/bin/*` as a non-root `appuser`. Previous application images stay available during builds and startup checks. Docker logs rotate at three 10 MB files per container.

Run `make clean` to reclaim artifacts from older deployments, or `make COMPOSE_ENV=dev clean` locally. It prunes dangling images (`docker image prune -f`), prunes BuildKit build cache (`docker builder prune -f`), and removes local build artifacts. It does not stop running application containers or remove PostgreSQL, Valkey, Beat volumes, `.env`, `data/` or `.venv/`.

Default resource caps, memory ceilings, and operational settings can be customized via the variables documented in `example.env`. Base images and tools are pinned by digest; builds remain local and target the host architecture.

Use Make to apply the `.env` browser switch. Raw `docker compose` commands require `--profile browser` when enabling the browser service; explicitly naming a profiled service bypasses profile selection.

See [administration](docs/administration.md) for role and trust commands, access rules and troubleshooting.

## Hybrid translation

Caption and explicit reply language detection runs locally with py3langid. Automatic media/post captions skip ignored languages, the selected target language, and text with insufficient alphabetic content; explicit `/translate [target]` replies and `Translate` button clicks override ignored languages and use either the per-request target or the chat/default target without mutating saved chat preferences.

Translation uses a Google-first online engine (`deep-translator` + `tenacity` with bounded 15s budget and `TRANSLATION_ONLINE_ENABLED` offline switch) with automatic atomic fallback to an on-demand CTranslate2 NLLB-200 int8 CPU model when online translation is disabled or unavailable. Account-based and paid providers (including Microsoft Translator F0) are deferred. When an automatic translated response exceeds Telegram's caption or message budget, the original post/media is preserved with a source-bound `Translate` button (encrypted 24-hour non-renewing Valkey snapshot) and multi-part results use `Continue translation` chains rather than `.txt` fallback.

The first required local fallback translation downloads about 630 MB of checksum-verified model assets into `TRANSLATION_MODEL_DIR`. Media, browser and compression workers mount the persistent `translation_models` volume at `/app/data/models`; the Docker images contain no model weights. Preserve the volume between deployments; its license and attribution files must travel with redistributed model assets. Each active worker child loads its own model, so measure memory before increasing concurrency. See [third-party notices](THIRD_PARTY_NOTICES.md) for the model's separate noncommercial license.

## Access and privacy

Bot authority, trust and group approval are separate. Only CLI operations can change bot-administrator authority or their personal restrictions.
Group bans and full bans on current Telegram administrators suspend group processing for everyone. Group processing also requires fresh administrator evidence.

The bot retains the identity and operational data needed for access control and its features. Temporary message cache entries are encrypted per chat and exclude contacts, locations, user profiles, forwarding metadata and nested replies.
Operational logs omit message URLs, exception contents and credentials while retaining operation names, exception types and code locations for debugging.

## Development

Built with Python 3.15+, aiogram, SQLAlchemy/PostgreSQL, Valkey, Celery and Dishka; pydoll-python is installed only in the browser-worker image target.
See the [contributor guide](CONTRIBUTING.md) for setup, checks and contribution conventions, and [architecture](docs/architecture.md) for implementation details.

## License

Application code: [BSD 3-Clause](LICENSE). Separately downloaded NLLB translation model assets: **CC BY-NC 4.0, noncommercial use only**. See [third-party notices](THIRD_PARTY_NOTICES.md) for model attribution, the full license, and commercial-use restrictions.

Optional local voice/audio transcription is documented in [speech recognition](docs/speech-recognition.md). Prepare its pinned model with `make prepare-speech`; keep `SPEECH_ENABLED=False` until the target-server capacity release gate passes.
