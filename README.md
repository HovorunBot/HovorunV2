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

Install Docker with Compose and `make`, then run:

```bash
make setup
```

Edit the generated `.env` file:

```dotenv
BOT_TOKEN=...
CACHE_ENCRYPTION_KEY=...
```

Both values are required. Provision an owner using your numeric Telegram user ID; the example ID below is synthetic:

```bash
docker compose --profile prod run --rm --no-deps bot uv run hovorun-admin role add 123456789 --role owner
make run
```

The container entrypoint applies database migrations. CLI administration can run before the bot starts and does not require Telegram or Valkey connections.

| Command | Purpose |
| --- | --- |
| `make run` | Start the bot and Valkey |
| `make stop` | Stop services |
| `make update` | Pull changes and rebuild |
| `make migrate` | Apply database migrations in Docker |

See [administration](docs/administration.md) for role and trust commands, access rules and troubleshooting.

## Access and privacy

Bot authority, trust and group approval are separate. Only CLI operations can change bot-administrator authority or their personal restrictions.
Group bans and full bans on current Telegram administrators suspend group processing for everyone. Group processing also requires fresh administrator evidence.

The bot retains the identity and operational data needed for access control and its features. Temporary message cache entries are encrypted per chat and exclude contacts, locations, user profiles, forwarding metadata and nested replies.
Operational logs omit message URLs, exception contents and credentials while retaining operation names, exception types and code locations for debugging.

## Development

Built with Python 3.15+, aiogram, SQLAlchemy/SQLite, Valkey, Dishka and DrissionPage.
See the [contributor guide](CONTRIBUTING.md) for setup, checks and contribution conventions, and [architecture](docs/architecture.md) for implementation details.

## License

[BSD 3-Clause](LICENSE).
