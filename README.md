# HovorunV2

**The ultimate wingman for your Telegram group chats — making them smarter, faster, and way more fun.**

HovorunV2 is a feature-rich Telegram chatbot designed to level up your group conversations with smart utilities and
seamless integrations.

---

## 🚀 Project Status: Alpha

HovorunV2 is currently in its **Alpha** stage. I'm actively building out core features and refining the experience.

### ⚠️ Current Hosting Model

At this stage, **HovorunV2 is self-hosted only.**
To use the bot, you must run your own instance using Docker.

> **Future Note:** I am planning to launch an **official public server** in the future, which will allow you to simply
> add the bot to your chats without any technical setup. Stay tuned!

---

## ✨ Features

- **🐦 Twitter (X) Integration**: Automatically detects Twitter/X links and provides rich previews:
    - Full tweet text and media (photos/videos) in high quality.
    - Support for quoted tweets.
    - Automatic translation to Ukrainian for foreign-language posts.
    - Engagement metrics (likes, retweets, views).
- **🎬 TikTok Integration**: Seamlessly share TikTok videos and slideshows:
    - Automatically extracts and attaches the original video content.
    - Full support for image-based slideshows.
    - Rich captions including author, video title, and music information.
- **📸 Instagram Reels**: Automatically detects Reels links and provides native video previews.
- **📺 YouTube Shorts**: Seamlessly share YouTube Shorts with high-quality video attachments.
- **🧵 Threads Integration**: Rich previews for Threads posts, including text, media, and translation.
- **🛡️ Chat Whitelisting**: Secure your bot by restricting it to specific chats. Admins can easily allow or disallow the bot in any group.
- **🌍 Dynamic Translation**: Automatically translates shared content into the chat's target language (default: Ukrainian).

---

## 🛠️ Getting Started

This project uses a `Makefile` and Docker to automate everything from tool installation to execution.

### Prerequisites

- `make`
- `docker`
- `uv` (for local development)

### Quick Start

1. **Setup**:
   ```bash
   make setup
   ```

2. **Configure Secrets**:
   Open the generated `.env` file and add your Telegram Bot Token and Admin IDs:
   ```bash
   BOT_TOKEN=your_telegram_bot_token_here
   ADMIN_IDS=[123,456]
   OPENROUTER_API_KEY=...
   ```

3. **Launch**:
   ```bash
   make run
   ```

---

## 📖 Available Commands

| Command        | Description                                                       |
|----------------|-------------------------------------------------------------------|
| `make setup`   | Prepares `.env`, data directory, and builds Docker images.        |
| `make run`     | Starts the production environment (Bot + Valkey) in Docker.       |
| `make stop`    | Stops all Docker services.                                        |
| `make run-dev` | Starts Valkey in Docker and runs Bot locally (no Docker for Bot). |
| `make update`  | Pulls the latest changes and rebuilds images.                     |

---

## 🏗️ Architecture (For Developers)

- **Domain**: Core entities and SQLAlchemy 2.0 database models.
- **Infrastructure**: Low-level implementation of repositories, Valkey async caching, and centralized DI container.
- **Application**:
    - **Data Services**: Orchestrate database transactions and repository operations.
    - **Business Services**: Pure business logic (Translation, Whitelisting, Language Management).
    - **Clients**: Specialized scrapers for external platforms (Twitter, TikTok, Threads).
    - **Media**: Binary IO and download management.
- **Interface**: Idiomatic Telegram bot handlers using `aiogram` routers and middlewares.

---

## 🧪 Development

**Run Tests**:
```bash
PYTHONPATH=src uv run pytest
```

**Linting**:
```bash
uv run ruff check .
```

**Type Checking**:
```bash
uv run ty check src
```

---

## 📄 License

HovorunV2 is released under the **BSD 3-Clause License**. See the [LICENSE](LICENSE) file for details.

---

**Developed with ❤️ by TwilightSparkle42**
