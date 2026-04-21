"""Module for handling Twitter/X post links in messages using VxTwitter API."""

import asyncio
import html
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    User,
)

from logger_conf import get_logger

from .base import BaseCommand, register_command


@dataclass
class MediaItem:
    """Represents a single media item (photo, video, or gif) from a tweet."""

    type: str
    url: str


@dataclass
class TweetData:
    """Consolidated data for a tweet."""

    author_name: str
    author_handle: str
    post_date: str
    post_content: str
    retweets: int
    likes: int
    views: int
    post_url: str
    is_quote: bool = False
    orig_author_name: str = ""
    orig_author_handle: str = ""
    orig_post_content: str = ""
    has_quote_media: bool = False
    media: list[MediaItem] = field(default_factory=list)


@register_command
class TwitterCommand(BaseCommand):
    """Command for interacting with Twitter and processing tweet links."""

    def __init__(self) -> None:
        """Initialize TwitterCommand and its logger."""
        self.logger = get_logger(__name__)

    TWITTER_URL_PATTERN = re.compile(
        r"https?://(?:www\.)?(?:api\.)?(?:x\.com|twitter\.com)/(?:\w+/status/|2/tweets/)(?P<post_id>\d+)",
    )

    HEADER_TEMPLATE = (
        '🗣 Shared by <a href="tg://user?id={tg_user_id}">{tg_user_name}</a>\n'
        '🐦 <b>{author_name}</b> (<a href="https://x.com/{author_handle}">@{author_handle}</a>)\n\n'
    )

    QUOTE_TEMPLATE = (
        "<blockquote expandable>\n"
        '🔄 <b>Original by {orig_author_name}</b> (<a href="https://x.com/{orig_author_handle}">@{orig_author_handle}</a>):\n'
        "{orig_post_content}\n"
        "</blockquote>"
    )

    FOOTER_TEMPLATE = '\n\n🔁 {retweets} | ❤️ {likes} | 👁️ {views}\n🔗 <a href="{post_url}">Open post</a>'
    SHORT_MESSAGE_LENGTH: int = 1000

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains a valid Twitter or X link.

        Args:
            message: The Telegram message to check.

        Returns:
            True if a valid link is found, False otherwise.
        """
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return bool(re.search(self.TWITTER_URL_PATTERN, message.text))

    async def handle(self, message: Message, bot: Bot) -> None:
        """Process Twitter/X links found in the message.

        Args:
            message: The Telegram message containing the links.
            bot: The Telegram bot instance.
        """
        if not message.text or not message.from_user:
            return

        links = list(re.finditer(self.TWITTER_URL_PATTERN, message.text))
        self.logger.info("Processing %d link(s) from user %s", len(links), message.from_user.id)
        for link in links:
            await self._process_single_link(message, bot, link)

    async def _process_single_link(self, message: Message, bot: Bot, link: re.Match) -> None:
        """Handle processing for a single Twitter/X link.

        Args:
            message: The original Telegram message.
            bot: The Telegram bot instance.
            link: The regex match object for the link.
        """
        post_id = link.group("post_id")
        post_url = link.group(0)

        # OPTIMIZATION 1: Share a single aiohttp session for all network requests in this pipeline
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                tweet = await self._get_tweet_data(session, post_id, post_url)
                if tweet:
                    await self._send_telegram_response(bot, message, tweet, session)
            except Exception:
                self.logger.exception("Failed to process X/Twitter link %s", post_url)

    async def _get_tweet_data(self, session: aiohttp.ClientSession, post_id: str, post_url: str) -> TweetData | None:
        """Fetch and parse tweet data, including translations.

        Args:
            session: The aiohttp session for requests.
            post_id: The ID of the tweet.
            post_url: The original URL of the tweet.

        Returns:
            The parsed TweetData or None if it fails.
        """
        api_data = await self._fetch_tweet_data(session, post_id)
        tweet = self._parse_api_response(api_data, post_url)

        # OPTIMIZATION 2: Run translations concurrently
        async def _safe_translate(text: str) -> str | None:
            """Safe wrapper for translation that handles empty text."""
            if not text or not text.strip():
                return None
            return await self._translate_text_if_needed(session, html.unescape(text))

        trans_main, trans_quote = await asyncio.gather(
            _safe_translate(tweet.post_content), _safe_translate(tweet.orig_post_content if tweet.is_quote else "")
        )

        if trans_main:
            tweet.post_content += f"\n\n🇺🇦 <b>Translated:</b>\n{html.escape(trans_main)}"
        if trans_quote:
            tweet.orig_post_content += f"\n\n🇺🇦 <b>Translated:</b>\n{html.escape(trans_quote)}"

        return tweet

    async def _translate_text_if_needed(self, session: aiohttp.ClientSession, text: str) -> str | None:
        """Translates text to Ukrainian if source is not English, Russian, or Ukrainian."""
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "uk", "dt": "t", "q": text}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == HTTPStatus.OK:
                    data = await resp.json()
                    src_lang = data[2]

                    # Ignore EN, RU, UK, and Undefined
                    if src_lang not in ("en", "ru", "uk", "und"):
                        return "".join([sentence[0] for sentence in data[0] if sentence[0]])
        except Exception:
            self.logger.exception("Google Translate API call failed")

        return None

    async def _fetch_tweet_data(self, session: aiohttp.ClientSession, post_id: str) -> dict[str, Any]:
        """Fetch tweet data from VxTwitter API.

        Args:
            session: The aiohttp session.
            post_id: The ID of the tweet.

        Returns:
            The raw JSON response as a dictionary.

        Raises:
            ValueError: If the API returns a non-200 status.
        """
        api_url = f"https://api.vxtwitter.com/i/status/{post_id}"
        async with session.get(api_url) as response:
            if response.status != HTTPStatus.OK:
                msg = f"VxTwitter API returned HTTP {response.status}"
                raise ValueError(msg)
            return await response.json()

    def _parse_api_response(self, data: dict[str, Any], post_url: str) -> TweetData:
        """Parse VxTwitter API response into TweetData object.

        Args:
            data: The raw API response dictionary.
            post_url: The original tweet URL.

        Returns:
            The parsed TweetData object.
        """
        date_epoch = data.get("date_epoch", 0)
        post_date = datetime.fromtimestamp(date_epoch, tz=UTC).strftime("%Y-%m-%d") if date_epoch else "Unknown Date"

        raw_text = data.get("text", "")
        qrt_url = data.get("qrtURL")

        if qrt_url and qrt_url in raw_text:
            raw_text = raw_text.replace(qrt_url, "").strip()

        tweet = TweetData(
            author_name=html.escape(data.get("user_name", "Unknown")),
            author_handle=html.escape(data.get("user_screen_name", "unknown")),
            post_date=post_date,
            post_content=html.escape(raw_text),
            retweets=data.get("retweets", 0),
            likes=data.get("likes", 0),
            views=data.get("replies", 0),
            post_url=post_url,
        )

        tweet.media = self._extract_media(data)
        self._extract_quote(tweet, data)

        return tweet

    def _extract_quote(self, tweet: TweetData, data: dict[str, Any]) -> None:
        """Extract quote tweet information if present.

        Args:
            tweet: The TweetData object to update.
            data: The raw API response dictionary.
        """
        quote_data = data.get("qrt", {})
        if not quote_data:
            return

        tweet.is_quote = True
        tweet.orig_author_name = html.escape(quote_data.get("user_name", "Unknown"))
        tweet.orig_author_handle = html.escape(quote_data.get("user_screen_name", "unknown"))
        tweet.orig_post_content = html.escape(quote_data.get("text", ""))

        quoted_media = self._extract_media(quote_data)
        if quoted_media:
            tweet.has_quote_media = True
            tweet.media.extend(quoted_media)

    def _extract_media(self, data: dict[str, Any]) -> list[MediaItem]:
        """Extract media items from API response data.

        Args:
            data: The API response dictionary.

        Returns:
            A list of extracted MediaItem objects.
        """
        media_items = []
        media_extended = data.get("media_extended", [])

        for m_obj in media_extended:
            m_type = m_obj.get("type")
            m_url = m_obj.get("url")

            if not m_type or not m_url:
                continue

            if m_type == "image":
                media_items.append(MediaItem(type="photo", url=m_url))
            elif m_type in ("video", "gif"):
                media_items.append(MediaItem(type="video", url=m_url))

        return media_items

    async def _download_media(
        self, session: aiohttp.ClientSession, media_list: list[MediaItem]
    ) -> list[InputMediaAudio | InputMediaDocument | InputMediaPhoto | InputMediaVideo]:
        """Download media into RAM using the provided shared session.

        Args:
            session: The aiohttp session.
            media_list: List of MediaItem objects to download.

        Returns:
            List of InputMedia objects for Telegram.
        """
        input_media = []
        for idx, m in enumerate(media_list):
            try:
                async with session.get(m.url) as response:
                    if response.status != HTTPStatus.OK:
                        continue
                    content = await response.read()
                    if m.type == "photo":
                        file = BufferedInputFile(content, filename=f"image_{idx}.jpg")
                        input_media.append(InputMediaPhoto(media=file))
                    elif m.type == "video":
                        file = BufferedInputFile(content, filename=f"video_{idx}.mp4")
                        input_media.append(InputMediaVideo(media=file))
            except Exception:
                self.logger.exception("Failed to download media to memory (%s)", m.url)

        return input_media

    def _prepare_url_media(
        self, media_items: list[MediaItem]
    ) -> list[InputMediaAudio | InputMediaDocument | InputMediaPhoto | InputMediaVideo]:
        """Prepare InputMedia objects using raw URLs for optimistic Telegram fetching.

        Args:
            media_items: List of MediaItem objects.

        Returns:
            List of InputMedia objects.
        """
        media_group = []
        for m in media_items:
            if m.type == "photo":
                media_group.append(InputMediaPhoto(media=m.url))
            elif m.type == "video":
                media_group.append(InputMediaVideo(media=m.url))
        return media_group

    async def _send_telegram_response(
        self, bot: Bot, message: Message, tweet: TweetData, session: aiohttp.ClientSession
    ) -> None:
        """Construct and send the Telegram response for a tweet.

        Args:
            bot: The Telegram bot instance.
            message: The original message.
            tweet: The parsed TweetData.
            session: The aiohttp session for media downloads.
        """
        if not message.from_user:
            return

        header, footer = self._format_tweet_message(tweet, message.from_user)

        full_content = tweet.post_content
        if tweet.is_quote:
            quote_content = tweet.orig_post_content
            if tweet.has_quote_media:
                quote_content += "\n\n<i>[📎 Quoted media included in album]</i>"

            quote_section = self.QUOTE_TEMPLATE.format(
                orig_author_name=tweet.orig_author_name,
                orig_author_handle=tweet.orig_author_handle,
                orig_post_content=quote_content,
            )
            full_content += f"\n\n{quote_section}"

        total_text = header + full_content + footer

        if not tweet.media:
            await self._send_chunked_text(bot, message.chat.id, message.message_id, full_content, header, footer)
            return

        if len(total_text) <= self.SHORT_MESSAGE_LENGTH:
            await self._send_short_media(bot, message.chat.id, message.message_id, tweet.media, total_text, session)
        else:
            await self._send_long_media(
                bot, message.chat.id, message.message_id, full_content, header, footer, tweet.media, session
            )

    async def _send_short_media(
        self,
        bot: Bot,
        chat_id: int,
        reply_to_id: int,
        media_items: list[MediaItem],
        text: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Send a tweet with media when the total text fits in a caption (<= 1024 chars).

        Attempts URL media first. Falls back to sending text + downloading to RAM.

        Args:
            bot: The Telegram bot instance.
            chat_id: The ID of the chat.
            reply_to_id: The ID of the message to reply to.
            media_items: List of media items.
            text: The caption text.
            session: The aiohttp session.
        """
        url_media_group = self._prepare_url_media(media_items)
        url_media_group[0].caption = text
        url_media_group[0].parse_mode = "HTML"

        try:
            await bot.send_media_group(chat_id=chat_id, media=url_media_group, reply_to_message_id=reply_to_id)
        except TelegramBadRequest as e:
            if "WEBPAGE_MEDIA_EMPTY" not in str(e) and "failed to get" not in str(e).lower():
                self.logger.exception("Unexpected Telegram API error during short media URL fetch")
                raise
        else:
            return

        # Fallback RAM approach
        placeholder_msg = await bot.send_message(
            chat_id=chat_id,
            text=text + "\n\n⏳ <i>Downloading media...</i>",
            parse_mode="HTML",
            reply_to_message_id=reply_to_id,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        downloaded_media = await self._download_media(session, media_items)

        if not downloaded_media:
            self.logger.warning("All media failed to download to RAM.")
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=text + "\n\n⚠️ <i>Failed to download media.</i>",
                    chat_id=chat_id,
                    message_id=placeholder_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            return

        downloaded_media[0].caption = text
        downloaded_media[0].parse_mode = "HTML"

        try:
            await bot.send_media_group(chat_id=chat_id, media=downloaded_media, reply_to_message_id=reply_to_id)
            with suppress(TelegramBadRequest):
                await placeholder_msg.delete()
        except Exception:
            self.logger.exception("Failed to upload RAM media to Telegram")
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=text + "\n\n⚠️ <i>Failed to upload media.</i>",
                    chat_id=chat_id,
                    message_id=placeholder_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

    async def _send_long_media(
        self,
        bot: Bot,
        chat_id: int,
        reply_to_id: int,
        content: str,
        header: str,
        footer: str,
        media_items: list[MediaItem],
        session: aiohttp.ClientSession,
    ) -> None:
        """Send a tweet with media when the total text exceeds caption limits.

        Sends text chunks first, then optimistically attaches media. Falls back to RAM if needed.

        Args:
            bot: The Telegram bot instance.
            chat_id: The ID of the chat.
            reply_to_id: The ID of the message to reply to.
            content: The tweet content.
            header: The message header.
            footer: The message footer.
            media_items: List of media items.
            session: The aiohttp session.
        """
        text_messages = await self._send_chunked_text(bot, chat_id, reply_to_id, content, header, footer)
        first_text_msg = text_messages[0]
        last_text_msg = text_messages[-1]

        url_media_group = self._prepare_url_media(media_items)
        url_media_group[0].caption = "📎 <b>Attached media.</b>"
        url_media_group[0].parse_mode = "HTML"

        try:
            await bot.send_media_group(
                chat_id=chat_id,
                media=url_media_group,
                reply_to_message_id=first_text_msg.message_id,
            )
        except TelegramBadRequest as e:
            if "WEBPAGE_MEDIA_EMPTY" not in str(e) and "failed to get" not in str(e).lower():
                self.logger.exception("Unexpected Telegram API error during long media URL fetch")
                raise
        else:
            return

        # Fallback RAM approach
        original_last_text = last_text_msg.html_text or ""
        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                text=original_last_text + "\n\n⏳ <i>Downloading media...</i>",
                chat_id=chat_id,
                message_id=last_text_msg.message_id,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

        downloaded_media = await self._download_media(session, media_items)

        if not downloaded_media:
            self.logger.warning("All media failed to download to RAM (Long Media).")
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=original_last_text + "\n\n⚠️ <i>Failed to download media.</i>",
                    chat_id=chat_id,
                    message_id=last_text_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            return

        downloaded_media[0].caption = "📎 <b>Attached media.</b>"
        downloaded_media[0].parse_mode = "HTML"

        try:
            await bot.send_media_group(
                chat_id=chat_id, media=downloaded_media, reply_to_message_id=first_text_msg.message_id
            )
            # Remove the downloading suffix on success
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=original_last_text,
                    chat_id=chat_id,
                    message_id=last_text_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
        except Exception:
            self.logger.exception("Failed to upload long RAM media to Telegram")
            with suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    text=original_last_text + "\n\n⚠️ <i>Failed to upload media.</i>",
                    chat_id=chat_id,
                    message_id=last_text_msg.message_id,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

    async def _send_chunked_text(
        self, bot: Bot, chat_id: int, reply_to_id: int, content: str, header: str, footer: str
    ) -> list[Message]:
        """Send long text in multiple chunks.

        Args:
            bot: The Telegram bot instance.
            chat_id: The ID of the chat.
            reply_to_id: The ID of the message to reply to.
            content: The tweet content.
            header: The message header.
            footer: The message footer.

        Returns:
            A list of sent Message objects.
        """
        chunks = self._chunk_text(content, max_length=3500)
        link_preview = LinkPreviewOptions(is_disabled=True)
        sent_messages = []

        first_bot_msg = await bot.send_message(
            chat_id=chat_id,
            text=header + chunks[0] + (footer if len(chunks) == 1 else ""),
            parse_mode="HTML",
            reply_to_message_id=reply_to_id,
            link_preview_options=link_preview,
        )
        sent_messages.append(first_bot_msg)

        last_bot_msg = first_bot_msg
        for i, chunk in enumerate(chunks[1:]):
            is_last_chunk = i == len(chunks) - 2
            last_bot_msg = await bot.send_message(
                chat_id=chat_id,
                text=chunk + (footer if is_last_chunk else ""),
                parse_mode="HTML",
                reply_to_message_id=last_bot_msg.message_id,
                link_preview_options=link_preview,
            )
            sent_messages.append(last_bot_msg)

        return sent_messages

    def _format_tweet_message(self, tweet: TweetData, tg_user: User) -> tuple[str, str]:
        """Format the tweet header and footer.

        Args:
            tweet: The TweetData object.
            tg_user: The Telegram user who shared the link.

        Returns:
            A tuple of (header, footer) strings.
        """
        header = self.HEADER_TEMPLATE.format(
            tg_user_id=tg_user.id,
            tg_user_name=html.escape(tg_user.full_name),
            author_name=tweet.author_name,
            author_handle=tweet.author_handle,
        )

        safe_post_url = html.escape(tweet.post_url)
        footer = self.FOOTER_TEMPLATE.format(
            retweets=tweet.retweets,
            likes=tweet.likes,
            views=tweet.views,
            post_url=safe_post_url,
        )
        return header, footer

    def _chunk_text(self, text: str, max_length: int) -> list[str]:
        """Split text into chunks that fit within Telegram's message limit.

        Args:
            text: The text to split.
            max_length: The maximum length of each chunk.

        Returns:
            A list of text chunks.
        """
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            split_at = text.rfind("\n", 0, max_length)
            if split_at == -1:
                split_at = text.rfind(" ", 0, max_length)
            if split_at == -1:
                split_at = max_length

            chunks.append(text[:split_at].strip())
            text = text[split_at:].strip()

        return chunks
