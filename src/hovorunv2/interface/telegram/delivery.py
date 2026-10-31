"""Media delivery service for Telegram interface."""

import logging
from collections.abc import Mapping
from contextlib import suppress
from enum import StrEnum

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramEntityTooLarge
from aiogram.types import BufferedInputFile, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, MediaUnion, Message

from hovorunv2.application.media import rendering
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload

logger = logging.getLogger(__name__)


class DeliveryError(StrEnum):
    """Delivery error kinds for media delivery failures."""

    DOWNLOAD = "download"
    TOO_LARGE = "too_large"
    UPLOAD = "upload"


class TelegramMediaDelivery:
    """Delivers rich media payloads to Telegram chats."""

    CAPTION_LIMIT = rendering.CAPTION_LIMIT
    HEADER_TEMPLATE = rendering.HEADER_TEMPLATE
    QUOTE_TEMPLATE = rendering.QUOTE_TEMPLATE
    METRICS_TEMPLATE = rendering.METRICS_TEMPLATE
    FOOTER_TEMPLATE = rendering.FOOTER_TEMPLATE

    ERROR_TEXTS: Mapping[DeliveryError, str] = {
        DeliveryError.DOWNLOAD: "⚠️ <i>Failed to download media.</i>",
        DeliveryError.TOO_LARGE: "⚠️ <i>Media is too large, view on original resource.</i>",
        DeliveryError.UPLOAD: "⚠️ <i>Failed to upload media.</i>",
    }

    def __init__(self, media_downloader: MediaDownloader, session: aiohttp.ClientSession) -> None:
        self._media_downloader = media_downloader
        self._session = session

    async def deliver(self, bot: Bot, message: Message, payload: RichMediaPayload, platform_name: str) -> None:
        """Execute the standardized media delivery lifecycle."""
        if not message.from_user:
            return

        tg_user_name = message.from_user.full_name
        total_text = self.build_caption(payload, tg_user_name, platform_name)

        if not payload.media_items:
            await message.answer(
                total_text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return

        caption = await self._handle_caption_limits(message, payload, total_text, tg_user_name, platform_name)

        # 0. Pre-downloaded bytes (e.g., from headless browser session)
        if any(item.data is not None for item in payload.media_items):
            await self._send_predownloaded_bytes(bot, message, payload, caption)
            return

        # 1. Optimistic URL approach
        if await self._try_url_delivery(bot, message, payload, caption):
            return

        # 2. Fallback RAM approach
        await self._perform_ram_delivery(bot, message, payload, caption)

    def build_caption(self, payload: RichMediaPayload, tg_user_name: str, platform_name: str) -> str:
        """Build caption from payload using standardized format."""
        return rendering.build_caption(payload, tg_user_name, platform_name)

    def _build_input_media(
        self, media: str | BufferedInputFile, *, is_video: bool, caption: str | None
    ) -> InputMediaVideo | InputMediaPhoto:
        """Create InputMediaVideo or InputMediaPhoto with optional HTML caption."""
        parse_mode = "HTML" if caption else None
        if is_video:
            return InputMediaVideo(media=media, caption=caption, parse_mode=parse_mode)
        return InputMediaPhoto(media=media, caption=caption, parse_mode=parse_mode)

    async def _handle_caption_limits(
        self, message: Message, payload: RichMediaPayload, total_text: str, tg_user_name: str, platform_name: str
    ) -> str:
        """Send long captions separately if needed and return appropriate caption for media."""
        plan = rendering.plan_caption(payload, tg_user_name, platform_name, total_text)
        if plan.separate_text is not None:
            await message.answer(
                plan.separate_text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        return plan.caption

    async def _send_predownloaded_bytes(
        self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str
    ) -> None:
        """Send media items using pre-downloaded byte buffers."""
        final_group = self._predownloaded_group(payload, caption)

        if not final_group:
            return

        try:
            await bot.send_media_group(
                chat_id=message.chat.id,
                media=final_group,
                reply_to_message_id=message.message_id,
            )
        except TelegramEntityTooLarge:
            logger.warning("Pre-downloaded media too large for Telegram upload")
            await self._answer_delivery_error(message, caption, DeliveryError.TOO_LARGE)
        except TelegramBadRequest:
            logger.warning("Telegram rejected pre-downloaded media")
            await self._answer_delivery_error(message, caption, DeliveryError.UPLOAD)

    def _predownloaded_group(self, payload: RichMediaPayload, caption: str) -> list[MediaUnion]:
        final_group: list[MediaUnion] = []
        for i, item_meta in enumerate(payload.media_items):
            if item_meta.data is None:
                continue
            ext = "mp4" if item_meta.is_video else "jpg"
            file = BufferedInputFile(item_meta.data, filename=f"browser_{i}.{ext}")
            final_group.append(
                self._build_input_media(file, is_video=item_meta.is_video, caption=caption if not final_group else None)
            )

        return final_group

    async def _answer_delivery_error(self, message: Message, caption: str, error_type: DeliveryError) -> None:
        await message.answer(
            text=f"{caption}\n\n{self.ERROR_TEXTS[error_type]}",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    async def _try_url_delivery(self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str) -> bool:
        """Attempt delivery using optimistic URL approach."""
        media_group: list[MediaUnion] = [
            self._build_input_media(item.url, is_video=item.is_video, caption=caption if i == 0 else None)
            for i, item in enumerate(payload.media_items)
        ]
        try:
            await bot.send_media_group(
                chat_id=message.chat.id, media=media_group, reply_to_message_id=message.message_id
            )
        except TelegramEntityTooLarge:
            logger.warning("Media too large even for URL delivery")
            await self._answer_delivery_error(message, caption, DeliveryError.TOO_LARGE)
            return True
        except TelegramBadRequest as e:
            err_msg = str(e).upper()
            if not any(item in err_msg for item in ("WEBPAGE_CURL_FAILED", "FAILED TO GET")):
                logger.warning("Telegram rejected URL media without a fetch recovery")
                raise
        else:
            return True
        return False

    async def _perform_ram_delivery(self, bot: Bot, message: Message, payload: RichMediaPayload, caption: str) -> None:
        """Perform fallback delivery by downloading media to RAM first."""
        placeholder_msg = await message.answer(
            text=caption + "\n\n⏳ <i>Downloading media...</i>",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        downloaded_files = await self._media_downloader.download_batch(
            payload.media_items,
            prefix="media",
            session=self._session,
        )

        final_group = self._downloaded_group(payload.media_items, downloaded_files, caption)

        if not final_group:
            await self._edit_placeholder_error(
                bot, message.chat.id, placeholder_msg.message_id, caption, DeliveryError.DOWNLOAD
            )
            return

        try:
            await bot.send_media_group(
                chat_id=message.chat.id, media=final_group, reply_to_message_id=message.message_id
            )
        except TelegramEntityTooLarge:
            logger.warning("Media too large for Telegram upload")
            await self._edit_placeholder_error(
                bot, message.chat.id, placeholder_msg.message_id, caption, DeliveryError.TOO_LARGE
            )
        except TelegramBadRequest:
            logger.warning("Telegram rejected uploaded media")
            await self._edit_placeholder_error(
                bot, message.chat.id, placeholder_msg.message_id, caption, DeliveryError.UPLOAD
            )

        else:
            # Deleting a progress message is best effort after media acceptance, never a reason to resend it.
            with suppress(TelegramAPIError):
                await placeholder_msg.delete()

    def _downloaded_group(
        self, items: list[MediaItem], downloaded: list[BufferedInputFile | MediaFailure], caption: str
    ) -> list[MediaUnion]:
        group: list[MediaUnion] = []
        for item, content in zip(items, downloaded, strict=True):
            if not isinstance(content, BufferedInputFile):
                continue
            group.append(
                self._build_input_media(content, is_video=item.is_video, caption=caption if not group else None)
            )
        return group

    async def _edit_placeholder_error(
        self, bot: Bot, chat_id: int, message_id: int, caption: str, error_type: DeliveryError
    ) -> None:
        """Update placeholder message with specific error text."""
        text = f"{caption}\n\n{self.ERROR_TEXTS[error_type]}"
        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
