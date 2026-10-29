"""Pure OpenGraph, browser metadata and metrics parsing shared across execution runtimes."""

import json
import logging
import re
from collections.abc import Mapping

from hovorunv2.application.clients.config import BrowserFallbackConfig, OGConfig
from hovorunv2.application.dtos import MediaItem, RichMediaPayload
from hovorunv2.application.text import extract_og_metadata, format_number

logger = logging.getLogger(__name__)


class MediaParser:
    """Convert provider metadata into media DTOs without network, Telegram or bot dependencies."""

    def _extract_from_json_scripts(self, html_content: str) -> dict[str, object] | None:
        """Extract video_url or display_url from application/json script tags."""
        for json_str in re.findall(
            r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html_content, re.DOTALL
        ):
            if "video_url" not in json_str and "display_url" not in json_str:
                continue
            try:
                script_data = json.loads(json_str)
                video = self._find_key_in_json(script_data, "video_url")
                image = self._find_key_in_json(script_data, "display_url")
                if video or image:
                    return {"video": video, "image": image}
            except Exception:
                logger.debug("Failed to parse JSON script blob")
        return None

    def _build_browser_payload(
        self, results: Mapping[str, object], url: str, b_cfg: BrowserFallbackConfig
    ) -> RichMediaPayload:
        """Build RichMediaPayload from browser extraction dictionary."""
        media_url = results.get("video") or results.get("image")
        raw_title, raw_description = results.get("title"), results.get("description")
        title = raw_title if isinstance(raw_title, str) and raw_title else b_cfg.default_title
        description = raw_description if isinstance(raw_description, str) else ""
        if b_cfg.description_clean_pattern:
            description = b_cfg.description_clean_pattern.sub("", description).strip(' "')

        footer = (
            self._build_metrics_footer(
                ("❤️", results.get("likes")),
                ("👁️", results.get("views")),
                ("💬", results.get("comments")),
                prefix="📊 ",
            )
            or b_cfg.default_footer
        )

        return RichMediaPayload(
            author_name=b_cfg.default_author,
            author_handle=b_cfg.author_handle,
            author_url=b_cfg.author_url,
            content=f"<b>{title}</b>\n\n{description}".strip(),
            footer_text=footer,
            original_url=url,
            media_items=[MediaItem(url=str(media_url), is_video=bool(results.get("video")))],
        )

    def _parse_og_html(self, html_content: str, url: str, og_cfg: OGConfig) -> RichMediaPayload | None:
        """Parse OpenGraph metadata from HTML string into raw RichMediaPayload."""
        metadata = extract_og_metadata(html_content)
        if not metadata:
            return None

        og_url = metadata.get("url", "")
        is_video = (
            metadata.get("type") == "video"
            or "video" in metadata
            or bool(
                og_cfg.video_url_pattern
                and (og_cfg.video_url_pattern.search(url) or og_cfg.video_url_pattern.search(og_url))
            )
        )
        if is_video:
            return None

        raw_content = metadata.get("description") or metadata.get("title") or ""
        media_items = [MediaItem(url=metadata["image"], is_video=False)] if "image" in metadata else []
        if not raw_content and not media_items:
            return None

        title = metadata.get("title", "")
        author_name = og_cfg.default_author
        if og_cfg.title_separator and og_cfg.title_separator in title:
            author_name = title.split(og_cfg.title_separator, maxsplit=1)[0].strip() or og_cfg.default_author
        elif og_cfg.title_suffix and title:
            author_name = title.removesuffix(og_cfg.title_suffix).strip() or og_cfg.default_author

        return RichMediaPayload(
            author_name=author_name,
            author_handle=og_cfg.author_handle,
            author_url=og_cfg.author_url,
            content=raw_content,
            original_url=url,
            media_items=media_items,
        )

    def _build_metrics_footer(
        self,
        *metrics: tuple[str, object],
        prefix: str = "",
        include_zero: bool = False,
    ) -> str:
        """Format (emoji, value) pairs into a standardized footer string."""
        parts: list[str] = []
        for emoji, val in metrics:
            if val is None:
                continue
            if isinstance(val, int):
                if val or include_zero:
                    parts.append(f"{emoji} {format_number(val)}")
            elif isinstance(val, str) and val:
                parts.append(f"{emoji} {val}")

        if not parts:
            return ""
        joined = " | ".join(parts)
        return f"{prefix}{joined}" if prefix else joined

    def _find_key_in_json(self, data: object, target_key: str) -> object:
        """Recursively find target_key in nested dicts/lists."""
        match data:
            case dict() if target_key in data:
                return data[target_key]
            case dict():
                items = data.values()
            case list():
                items = data
            case _:
                return None

        for item in items:
            if (result := self._find_key_in_json(item, target_key)) is not None:
                return result
        return None
