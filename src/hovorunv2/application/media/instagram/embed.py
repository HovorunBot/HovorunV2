"""Build the allowlisted vxinstagram status route from validated Instagram facts."""

import base64

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.results import InstagramPost, InstagramResult

VXINSTAGRAM_BASE_URL = "https://vxinstagram.com"


def build_status_url(post: InstagramPost, *, base_url: str = VXINSTAGRAM_BASE_URL) -> str:
    """Return the public status route for one canonical post without tracking data.

    Args:
        post: Validated canonical Instagram identity.
        base_url: Public embed service origin.

    Returns:
        The observed `/api/v1/statuses/{context}` route for the requested post.
    """
    context = f"{post.shortcode}&0&https://instagram.com/{post.kind}/{post.shortcode}/"
    encoded = base64.urlsafe_b64encode(context.encode()).decode().rstrip("=")
    return f"{base_url}/api/v1/statuses/{encoded}"


class DisabledEmbedAdapter:
    """A no-request embed fallback for deployments that opt out of the public service."""

    def extract(self, post: InstagramPost, deadline: ExtractionDeadline) -> InstagramResult:
        """Report the expected unavailable contract without opening a request.

        Args:
            post: Canonical requested Instagram identity.
            deadline: Shared extraction deadline, ignored by the disabled adapter.

        Raises:
            MediaExtractionError: Always, so the action chain continues to the next strategy.
        """
        del post, deadline
        msg = "Instagram embed fallback is disabled."
        raise MediaExtractionError(msg)
