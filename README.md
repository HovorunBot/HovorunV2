# HovorunV2

**The ultimate wingman for your Telegram group chats — making them smarter, faster, and way more fun.**

HovorunV2 is a feature-rich Telegram chatbot designed to level up your group conversations with smart utilities and
seamless integrations.

---

## 🚀 Project Status: Alpha

HovorunV2 is currently in its **Alpha** stage. I'm actively building out core features and refining the experience.

### ⚠️ Current Hosting Model

At this stage, **HovorunV2 is self-hosted only.**
To use the bot, you must run your own instance following the instructions below.

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
    - Automatic translation of video descriptions.
- **🛡️ Chat Whitelisting**: Secure your bot by restricting it to specific chats. Admins can easily allow or disallow the
  bot in any group.
- **🛠️ Debug Utilities**: Built-in tools for admins to check chat IDs and bot status on the fly.
- **More coming soon!** I'm working on more integrations to make your chats even better.

---

## 🛠️ Getting Started

This project uses a `Makefile` to automate everything from tool installation to execution.

### Prerequisites

- `make`
- `git`

The setup process automatically handles installing [uv](https://astral.sh/uv/) and Python 3.14.

### Quick Start

1. **Clone and Setup**:
   ```bash
   git clone https://github.com/HovorunBot/HovorunV2.git
   cd HovorunV2
   make setup
   ```

2. **Configure Secrets**:
   Open the generated `.env` file and add your Telegram Bot Token:
   ```bash
   BOT_TOKEN=your_telegram_bot_token_here
   ```

3. **Launch**:
   ```bash
   make run
   ```

---

## 📖 Available Commands

| Command           | Description                                                  |
|-------------------|--------------------------------------------------------------|
| `make setup`      | Installs tools, clones/updates code, and syncs dependencies. |
| `make run`        | Starts the bot in the foreground.                            |
| `make run-daemon` | Starts the bot in the background (logs to `hovorun.log`).    |
| `make stop`       | Gracefully stops the background process.                     |
| `make update`     | Pulls the latest changes and synchronizes your environment.  |

---

## 🏗️ Architecture (For Developers)

HovorunV2 follows the **Onion Architecture** to ensure the code remains modular and maintainable:

- **Domain**: Pure business logic and modern SQLAlchemy 2.0 database models.
- **Application**: Service layer for orchestration (Translation, Whitelisting, Caching).
- **Infrastructure**: Database repositories, disk caching, and configuration.
- **Interface**: Telegram bot handlers and command registration.

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

---

## 📄 License

HovorunV2 is released under the **BSD 3-Clause License**. See the [LICENSE](LICENSE) file for details.

---

**Developed with ❤️ by TwilightSparkle42**
