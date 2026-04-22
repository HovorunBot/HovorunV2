"""Database service module for managing whitelisted chats."""

import sqlite3
from pathlib import Path

from hovorunv2.logger_conf import get_logger

logger = get_logger(__name__)


class DatabaseService:
    """Service to handle SQLite database operations."""

    def __init__(self, db_path: str = "cache/data/hovorun.db") -> None:
        """Initialize database service.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whitelisted_chats (
                        chat_id INTEGER PRIMARY KEY
                    )
                    """
                )
                conn.commit()
            logger.info("Database initialized at %s", self.db_path)
        except sqlite3.Error:
            logger.exception("Failed to initialize database at %s", self.db_path)
            raise

    def is_chat_whitelisted(self, chat_id: int) -> bool:
        """Check if chat is in the whitelist.

        Args:
            chat_id: ID of the Telegram chat.

        Returns:
            True if whitelisted, False otherwise.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT 1 FROM whitelisted_chats WHERE chat_id = ?", (chat_id,))
                return cursor.fetchone() is not None
        except sqlite3.Error:
            logger.exception("Database error while checking whitelist for chat %d", chat_id)
            return False

    def add_to_whitelist(self, chat_id: int) -> None:
        """Add chat to the whitelist.

        Args:
            chat_id: ID of the Telegram chat.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT OR IGNORE INTO whitelisted_chats (chat_id) VALUES (?)", (chat_id,))
                conn.commit()
            logger.info("Chat %d added to whitelist", chat_id)
        except sqlite3.Error:
            logger.exception("Database error while adding chat %d to whitelist", chat_id)

    def remove_from_whitelist(self, chat_id: int) -> None:
        """Remove chat from the whitelist.

        Args:
            chat_id: ID of the Telegram chat.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM whitelisted_chats WHERE chat_id = ?", (chat_id,))
                conn.commit()
            logger.info("Chat %d removed from whitelist", chat_id)
        except sqlite3.Error:
            logger.exception("Database error while removing chat %d from whitelist", chat_id)
