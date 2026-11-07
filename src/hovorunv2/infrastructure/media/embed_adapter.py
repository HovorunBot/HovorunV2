"""Native vxinstagram adapter using a separate cookie-free bounded HTTP client."""

from hovorunv2.application.jobs.http_ports import HTTPTransportError
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.embed import VXINSTAGRAM_BASE_URL, build_status_url
from hovorunv2.application.media.instagram.parsing import parse_embed_response
from hovorunv2.application.media.instagram.results import InstagramPost, InstagramResult
from hovorunv2.infrastructure.http_transport.extraction_http import ExtractionHTTPClient


class VxInstagramEmbedAdapter:
    """Fetch the public status JSON and map it without reproducing the service's parser."""

    def __init__(self, *, base_url: str = VXINSTAGRAM_BASE_URL) -> None:
        self._base_url = base_url

    def extract(self, post: InstagramPost, deadline: ExtractionDeadline) -> InstagramResult:
        """Return usable public media within a shared deadline.

        Args:
            post: Canonical requested Instagram identity.
            deadline: Shared extraction deadline.

        Returns:
            Mapped embed candidate.

        Raises:
            MediaExtractionError: If the request or mapping fails without usable media.
        """
        url = build_status_url(post, base_url=self._base_url)
        try:
            with ExtractionHTTPClient(deadline) as http:
                raw = http.read_json(url)
        except HTTPTransportError:
            msg = "Instagram embed request failed."
            raise MediaExtractionError(msg) from None
        return parse_embed_response(raw, post)
