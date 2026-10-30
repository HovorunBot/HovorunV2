"""Common provider media selection and ordered JSON-script fallback contracts."""

import pytest

from hovorunv2.application.clients.config import BrowserFallbackConfig
from hovorunv2.application.clients.parsing import MediaParser
from hovorunv2.application.text import format_number

CONFIG = BrowserFallbackConfig("video", default_title="Synthetic post")
SOURCE = "https://synthetic.invalid/post"


class TestProviderParsing(MediaParser):
    """Exercise the parser extension contract through a concrete derived parser."""

    @pytest.mark.parametrize("results", [{}, {"video": None}, {"image": ""}])
    def test_browser_payload_requires_actual_media_metadata(self, results: dict[str, object]) -> None:
        """Absent media is a failed required build rather than a media item whose URL is the word None."""
        with pytest.raises(ValueError, match="usable media"):
            self._build_browser_payload(results, SOURCE, CONFIG)

    @pytest.mark.parametrize(
        ("results", "url", "video"),
        [
            ({"video": "synthetic-video", "image": "synthetic-image"}, "synthetic-video", True),
            ({"image": "synthetic-image"}, "synthetic-image", False),
            ({"video": "", "image": "synthetic-image"}, "synthetic-image", False),
        ],
    )
    def test_browser_media_preference_and_default_title_remain_stable(
        self, results: dict[str, object], url: str, *, video: bool
    ) -> None:
        """Video wins over image, while optional title/description fields retain configured defaults."""
        payload = self._build_browser_payload(results, SOURCE, CONFIG)
        assert payload.media_items[0].url == url
        assert payload.media_items[0].is_video is video
        assert payload.content == "<b>Synthetic post</b>"
        assert payload.original_url == SOURCE

    def test_json_scripts_skip_malformed_candidates_and_preserve_first_usable_media(self) -> None:
        """An invalid strategy candidate may fall through without replacing the first usable script."""
        html = """
        <script type="application/json">invalid video_url</script>
        <script type="application/json">{"nested":{"display_url":"first-image"}}</script>
        <script type="application/json">{"video_url":"later-video"}</script>
        """
        assert self._extract_from_json_scripts(html) == {"video": None, "image": "first-image"}


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(-1, "-1"), (999, "999"), (1000, "1K"), (1500, "1.5K"), (1000000, "1M"), (1000000000, "1B")],
)
def test_format_number_preserves_metric_thresholds(amount: int, expected: str) -> None:
    """Named shared thresholds retain the original decimal and whole-number rendering."""
    assert format_number(amount) == expected
