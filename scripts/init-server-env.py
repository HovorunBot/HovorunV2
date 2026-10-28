"""Generate private server configuration once, without overwriting an existing .env."""

# Standalone operator script; this directory is not an application package.
# ruff: noqa: INP001

import secrets
import sys
from pathlib import Path

ENV_FILE_MODE = 0o600


def main() -> None:
    """Keep committed development defaults separate from credentials generated on the server."""
    root = Path(__file__).resolve().parents[1]
    destination = root / ".env"
    if destination.exists():
        sys.stdout.write("Existing .env preserved.\n")
        return
    values = (root / "example.env").read_text()
    password = secrets.token_urlsafe(32)
    encryption_key = secrets.token_urlsafe(48)
    replacements = {
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg://hovorun:{password}@postgres:5432/hovorun",
        "CACHE_ENCRYPTION_KEY": encryption_key,
    }
    lines = []
    for line in values.splitlines():
        name, separator, _value = line.partition("=")
        lines.append(name + separator + replacements[name] if name in replacements else line)
    try:
        with destination.open("x") as output:
            destination.chmod(ENV_FILE_MODE)
            output.write("\n".join(lines) + "\n")
    except FileExistsError:
        sys.stdout.write("Existing .env preserved.\n")
        return
    sys.stdout.write("Generated private server .env. Set BOT_TOKEN before starting the bot.\n")


if __name__ == "__main__":
    main()
